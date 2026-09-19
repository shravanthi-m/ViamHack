import unittest
from unittest.mock import patch
from runtime.observer_audio import transcribe


class AudioTests(unittest.TestCase):
    @patch('runtime.observer_audio.credentials', return_value={'OPENROUTER_STT_MODEL': 'openai/whisper-1'})
    @patch('runtime.observer_audio.api_json', return_value={'text': '  Make the demo drink.  '})
    def test_transcription_contract(self, remote, _credentials):
        value = transcribe(b'\x1aE\xdf\xa3synthetic-test-audio', 'webm')
        endpoint, body = remote.call_args.args
        self.assertEqual(endpoint, 'audio/transcriptions')
        self.assertEqual(body['input_audio']['format'], 'webm')
        self.assertEqual(value['text'], 'Make the demo drink.')
        self.assertNotIn('plan', value)

    def test_bad_audio_never_reaches_network(self):
        with patch('runtime.observer_audio.api_json') as remote:
            for data, kind in ((b'', 'wav'), (b'not-a-wave', 'wav'), (b'audio', 'exe')):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    transcribe(data, kind)
            remote.assert_not_called()

    @patch('runtime.observer_audio.api_json', return_value={'text': ''})
    def test_empty_transcript_is_not_a_request(self, _remote):
        with self.assertRaisesRegex(ValueError, 'No speech'):
            transcribe(b'\x1aE\xdf\xa3test', 'webm')
