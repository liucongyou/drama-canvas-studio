import unittest
from unittest.mock import patch

import ark_video_contract
import provider_client


class ArkVideoContractTests(unittest.TestCase):
    def test_model_families_are_derived_from_remote_model(self):
        self.assertEqual(
            ark_video_contract.classify_model('doubao-seedance-2-5-260628').version,
            '2.5',
        )
        self.assertEqual(
            ark_video_contract.classify_model('doubao-seedance-2-0-mini-260615').version,
            '2.0',
        )
        self.assertEqual(
            ark_video_contract.classify_model('doubao-seedance-1-0-pro-250528').version,
            '1.0',
        )

    def test_seedance_20_rejects_more_than_nine_images(self):
        with self.assertRaisesRegex(ValueError, '最多 9 张'):
            ark_video_contract.validate_submission(
                remote_model='doubao-seedance-2-0-mini-260615',
                prompt='镜头',
                refs=[{'kind': 'image', 'url': f'https://x/{i}.png'} for i in range(10)],
                voices=[],
                params={'duration': 5},
            )

    def test_seedance_20_rejects_audio_only(self):
        with self.assertRaisesRegex(ValueError, '至少需要 1 张图'):
            ark_video_contract.validate_submission(
                remote_model='doubao-seedance-2-0-260128',
                prompt='镜头',
                refs=[],
                voices=[{'url': 'https://x/a.mp3'}],
                params={'duration': 5},
            )

    def test_seedance_25_allows_audio_only(self):
        contract = ark_video_contract.validate_submission(
            remote_model='doubao-seedance-2-5-260628',
            prompt='镜头',
            refs=[],
            voices=[{'url': 'https://x/a.mp3'}],
            params={'duration': 5},
        )
        self.assertEqual(contract.version, '2.5')

    def test_seedance_20_rejects_more_than_fifteen_seconds(self):
        with self.assertRaisesRegex(ValueError, '4-15 秒'):
            ark_video_contract.validate_submission(
                remote_model='doubao-seedance-2-0-260128',
                prompt='镜头',
                refs=[],
                voices=[],
                params={'duration': 16},
            )

    def test_seedance_25_rejects_seed_before_request(self):
        model = {
            'remoteModel': 'doubao-seedance-2-5-260628',
            'adapter': 'ark_video',
            'params': {
                'duration': {'min': 4, 'max': 30},
                'ratio': {'options': ['9:16']},
                'resolution': {'options': ['480P']},
            },
        }
        provider = {
            'id': 'ark',
            'base_url': 'https://ark.example/api/v3',
            'api_key': 'key',
        }
        with patch.object(provider_client, '_request_json') as request:
            with self.assertRaisesRegex(ValueError, '不支持 seed'):
                provider_client.generate_video(
                    provider,
                    model,
                    '镜头',
                    {'duration': 5, 'ratio': '9:16', 'resolution': '480P', 'seed': 7},
                    [],
                    [],
                )
        request.assert_not_called()

    def test_ark_video_marks_reference_task_and_output_format(self):
        model = {
            'remoteModel': 'doubao-seedance-2-5-260628',
            'adapter': 'ark_video',
            'params': {
                'duration': {'min': 4, 'max': 30},
                'ratio': {'options': ['9:16']},
                'resolution': {'options': ['480P']},
            },
        }
        provider = {
            'id': 'ark',
            'base_url': 'https://ark.example/api/v3',
            'api_key': 'key',
        }
        bodies = []

        def fake_request(method, url, key, body=None, timeout=120):
            if method == 'POST':
                bodies.append(body)
                return {'id': 'task-1'}
            return {'status': 'succeeded', 'content': {'video_url': 'https://x/v.mp4'}}

        with (
            patch.object(provider_client, '_request_json', side_effect=fake_request),
            patch.object(provider_client.provider_catalog, 'storage_config', return_value={}),
            patch.object(provider_client.tos_storage, 'is_verified', return_value=False),
        ):
            provider_client.generate_video(
                provider,
                model,
                '严格参考图1中的角色。',
                {'duration': 5, 'ratio': '9:16', 'resolution': '480P', 'outputFormat': 'mov'},
                [{'kind': 'image', 'url': 'https://x/a.png', 'label': '角色'}],
                [],
            )
        self.assertEqual(bodies[0]['omni_reference_task_type'], 'reference')
        self.assertEqual(bodies[0]['output_format'], 'mov')


if __name__ == '__main__':
    unittest.main()
