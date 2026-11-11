import base64
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from converter import Converter, KeyCache, Cancelled, ConversionError, ffmpeg_exe, run_ffmpeg, validate_flac, pcm_md5
from formats import inspect_file, FormatError
from qmc_crypto import QMC2, derive_key

EKEY = 'VGhpcyBpcyBHFWEh4cjZ1Vi7rJ56XeoPlqGM1sxBGPg7mt89umKclFBr9iqfmFdS'


class CryptoTests(unittest.TestCase):
    def test_independent_tea_vector(self):
        self.assertEqual(derive_key(EKEY), b'This is a test key for test purpose :D')

    def test_independent_map_vector(self):
        expected = bytes.fromhex('3f8ac1493f49c18a3f8ac1493f49c18a')
        for native in (False, True):
            self.assertEqual(QMC2(b'ABCDEFGHIJKLMNOP', native=native).decrypt(bytes(16)), expected)

    def test_native_python_boundaries(self):
        r = random.Random(20261110)
        for key in (r.randbytes(128), r.randbytes(301), r.randbytes(512), r.randbytes(768)):
            fast = QMC2(key); slow = QMC2(key, native=False)
            self.assertIsNotNone(fast.lib, 'Native decoder was not loaded')
            data = r.randbytes(20000)
            for offset in (0, 127, 128, 5119, 5120, 32766, 32767, 32768, 1048576, 5120*511):
                with self.subTest(key_length=len(key), offset=offset):
                    self.assertEqual(fast.decrypt(data, offset), slow.decrypt(data, offset))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        results = Path(__file__).resolve().parents[1] / 'test-results'
        results.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=results)
        cls.root = Path(cls.temp.name)
        cls.flac = cls.root / '测试歌手 - 中文歌曲.flac'
        subprocess.run([ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1.5:sample_rate=44100',
                        '-ac', '2', '-c:a', 'flac', str(cls.flac)], check=True)
        cls.plain = cls.flac.read_bytes()
        cls.resource = 'F000testresource.mflac'
        footer = bytearray(192)
        footer[12:12+len('testmid'.encode('utf-16-le'))] = 'testmid'.encode('utf-16-le')
        footer[72:72+len(cls.resource.encode('utf-16-le'))] = cls.resource.encode('utf-16-le')
        footer[-16:-12] = (192).to_bytes(4, 'little')
        footer[-12:-8] = (1).to_bytes(4, 'little'); footer[-8:] = b'musicex\0'
        cls.source = cls.root / '测试歌手 - 中文歌曲.mflac'
        cls.source.write_bytes(QMC2(derive_key(EKEY)).decrypt(cls.plain) + footer)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def engine(self, name, valid=True):
        cache = KeyCache(self.root / (name + '-keys.json'))
        cache.entries = {self.resource: EKEY} if valid else {}
        return Converter(cache)

    def test_musicex_footer_exact_audio_length(self):
        m = inspect_file(self.source)
        self.assertEqual(m.audio_size, len(self.plain))
        self.assertEqual(m.filename, self.resource)
        self.assertEqual(m.song_mid, 'testmid')

    def test_repeated_conflicting_footer_uses_original_metadata(self):
        repeated = self.root / 'repeated.mflac'
        outer = bytearray(self.source.read_bytes()[-192:])
        outer[12:72] = bytes(60)
        outer[12:12+len('wrongmid'.encode('utf-16-le'))] = 'wrongmid'.encode('utf-16-le')
        outer[72:172] = bytes(100)
        name = 'Q0M0differentresource.mflac'.encode('utf-16-le')
        outer[72:72+len(name)] = name
        repeated.write_bytes(self.source.read_bytes() + outer)
        m = inspect_file(repeated)
        self.assertEqual(m.audio_size, len(self.plain))
        self.assertEqual(m.filename, self.resource)
        self.assertEqual(m.song_mid, 'testmid')
        self.assertEqual(m.footer_count, 2)
        r = self.engine('repeated').convert(repeated, self.root/'repeated-output')
        self.assertEqual(r.status, 'success')

    def test_both_formats_and_existing_file_protection(self):
        output = self.root / 'both'
        engine = self.engine('both')
        before = self.source.read_bytes()
        result = engine.convert(self.source, output, 'both')
        self.assertEqual(len(result.outputs), 2)
        flac, mp3 = result.outputs
        self.assertEqual(mp3['bitrate'], 320000)
        self.assertEqual(mp3['channels'], 2)
        self.assertLess(abs(flac['duration_seconds']-mp3['duration_seconds']), .15)
        out = Path(flac['path']); original = out.read_bytes()
        result2 = engine.convert(self.source, output)
        self.assertNotEqual(result2.outputs[0]['path'], str(out))
        self.assertEqual(out.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(list(output.glob('.conversion-*')))

    def test_cancel_removes_temporary_files(self):
        engine = self.engine('cancel'); flag = threading.Event(); output = self.root / 'cancel'
        def progress(value):
            flag.set()
        with self.assertRaises(Cancelled):
            engine.convert(self.source, output, 'both', cancel=flag, progress=progress)
        self.assertEqual(list(output.iterdir()), [])

    def test_missing_key_has_actionable_error_and_no_output(self):
        output = self.root / 'missing'; engine = self.engine('missing', False)
        with self.assertRaisesRegex(ConversionError, '未内嵌密钥'):
            engine.convert(self.source, output)
        self.assertFalse(output.exists())

    def test_wrong_key_rejected_before_output(self):
        output = self.root / 'wrong'; engine = self.engine('wrong')
        engine.cache.entries[self.resource] = base64.b64encode(b'invalid key').decode()
        with self.assertRaises(Exception):
            engine.convert(self.source, output)
        self.assertFalse(output.exists())

    def test_truncated_file_rejected(self):
        broken = self.root / 'broken.mflac'; broken.write_bytes(self.source.read_bytes()[:37])
        with self.assertRaises(FormatError):
            inspect_file(broken)

    def test_nonstandard_packets_normalized_only_with_matching_pcm_md5(self):
        from mutagen.flac import FLAC
        path = self.root / 'nonstandard.flac'
        path.write_bytes(self.plain + b'\xff\xf8\x00\x00'*128)
        expected = f'{FLAC(self.flac).info.md5_signature:032x}'
        with self.assertRaises(ConversionError):
            run_ffmpeg(['-i',str(path),'-map','0:a:0','-f','null','-'])
        self.assertEqual(pcm_md5(path, 16, strict=False), expected)
        self.assertTrue(validate_flac(path))
        self.assertEqual(pcm_md5(path,16),expected)
        run_ffmpeg(['-i',str(path),'-map','0:a:0','-f','null','-'])

    def test_corrupted_pcm_rejected_during_normalization(self):
        from mutagen.flac import FLAC
        path = self.root / 'invalid-pcm.flac'
        path.write_bytes(self.plain + b'\xff\xf8\x00\x00'*128)
        f = FLAC(path); f.info.md5_signature = 1; f.save()
        with self.assertRaisesRegex(ConversionError, 'MD5 不匹配'):
            validate_flac(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
