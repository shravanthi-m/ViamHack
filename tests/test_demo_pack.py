import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from runtime.__main__ import dispatch
from runtime.demo_pack import prepare, verify
from runtime.demonstrations import write
from runtime.teaching import teach_skill
from tests.test_teach_replay import FakeIO, config, yes


class DemoPackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'config.json'
        write(self.config_path, config())
        self.data = self.root / 'demonstrations'
        self.pack = self.root / 'pack'
        with patch('builtins.print'):
            for name in ('coconut_water', 'pitcher'):
                path = await teach_skill(name, FakeIO(), config(), root=self.data, ask_fn=yes)
                meta = json.loads((path / 'metadata.json').read_text())
                for observation in meta['observations'].values():
                    for view in observation['frames'].values():
                        for image in view['images']:
                            (path / image['path']).write_bytes(b'test image')

    def freeze(self):
        return prepare(self.config_path, self.data, self.pack)

    async def test_default_command_never_connects_and_pins_selection(self):
        self.freeze()
        # Later teaching cannot replace the frozen demo's selection.
        with patch('builtins.print'):
            await teach_skill('pitcher', FakeIO(), config(), root=self.data, ask_fn=yes)
        args = argparse.Namespace(command='prepared-demo', config=str(self.config_path),
                                  pack=str(self.pack), execute=False, runs=str(self.root / 'runs'))
        with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
            await dispatch(args)
            connect.assert_not_awaited()
        self.assertEqual(verify(self.pack, self.config_path)['status'],
                         'candidate_requires_physical_rehearsal')

    async def test_changed_trajectory_blocks_before_connection(self):
        self.freeze()
        path = next(self.pack.glob('demonstrations/*/*/trajectory.json'))
        path.write_text('{}')
        args = argparse.Namespace(command='prepared-demo', config=str(self.config_path),
                                  pack=str(self.pack), execute=True, runs=str(self.root / 'runs'))
        with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(ValueError, 'file changed'):
                await dispatch(args)
            connect.assert_not_awaited()

    def test_config_drift_and_extra_files_block(self):
        self.freeze()
        original = self.config_path.read_text()
        self.config_path.write_text(original + '\n')
        with self.assertRaisesRegex(ValueError, 'dependency changed'):
            verify(self.pack, self.config_path)
        self.config_path.write_text(original)
        (self.pack / 'extra.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'file list changed'):
            verify(self.pack, self.config_path)

    def test_missing_image_and_bad_track_cannot_produce_a_manifest(self):
        path = next(self.data.glob('pitcher/*/trajectory.json'))
        original = path.read_text()
        track = json.loads(original)
        track['waypoints'][1]['joints'][0] = 21
        write(path, track)
        with self.assertRaisesRegex(ValueError, 'joint step'):
            self.freeze()
        self.assertFalse(self.pack.exists())
        path.write_text(original)
        next(self.data.glob('pitcher/*/*.jpg')).unlink()
        with self.assertRaises(FileNotFoundError):
            self.freeze()
        self.assertFalse((self.pack / 'manifest.json').exists())

    def test_vision_profile_and_measurement_dependencies_are_frozen(self):
        evidence = self.root / 'measured-image.png'
        evidence.write_bytes(b'physical measurement fixture')
        profile = self.root / 'vision-profile.json'
        from runtime.demo_pack import digest
        write(profile, {'evidence_sha256': {str(evidence): digest(evidence)}})
        cfg = config()
        cfg['primitive_settings']['replay_vision'] = {'profiles': {
            name: str(profile) for name in ('coconut_water', 'pitcher')}}
        write(self.config_path, cfg)
        # Profile physics are exercised by test_replay_vision; here test pack integrity.
        with patch('primitives.replay_vision.load_profile'):
            self.freeze()
            verify(self.pack, self.config_path)
            original = profile.read_text()
            profile.write_text(original+'\n')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                verify(self.pack, self.config_path)
            profile.write_text(original)
            evidence.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                verify(self.pack, self.config_path)

    def test_existing_pack_never_overwritten(self):
        self.freeze()
        before = (self.pack / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertEqual(before, (self.pack / 'manifest.json').read_bytes())
