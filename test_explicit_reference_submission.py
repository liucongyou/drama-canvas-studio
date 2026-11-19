import unittest
from unittest.mock import patch

import canvas_store
import recipe_contract
import server
import tenant_context


class RequestReferenceCandidateTests(unittest.TestCase):
    def test_merges_meta_and_params_then_adds_linked_slots(self):
        refs, explicit = server.request_reference_candidates(
            params={
                "refs": [{"nodeId": "params-only", "label": "旧请求快照"}],
            },
            meta={
                "refs": [{"nodeId": "meta-only", "label": "当前卡片槽位"}],
            },
            node_type="video",
            node_id="video-1",
            connections=[
                {
                    "fromNodeId": "linked-only",
                    "toNodeId": "video-1",
                },
            ],
            nodes=[
                {
                    "id": "linked-only",
                    "type": "image",
                    "title": "角色｜背景角色（图16）",
                    "metadata": {
                        "assetId": "character:背景角色:default",
                        "seriesId": "character:背景角色",
                        "variantKey": "default",
                    },
                },
            ],
        )
        self.assertTrue(explicit)
        self.assertEqual(
            [ref["nodeId"] for ref in refs],
            ["meta-only", "params-only", "linked-only"],
        )

    def test_deduplicates_a_link_already_in_the_slot_list(self):
        refs, explicit = server.request_reference_candidates(
            params={
                "refs": [{"nodeId": "asset-1", "label": "当前槽位"}],
            },
            meta={
                "refs": [
                    {"nodeId": "asset-1", "label": "当前槽位"},
                    {"nodeId": "asset-2", "label": "第二个槽位"},
                ],
            },
            node_type="video",
            node_id="video-1",
            connections=[
                {"fromNodeId": "asset-1", "toNodeId": "video-1"},
                {"fromNodeId": "asset-2", "toNodeId": "video-1"},
            ],
            nodes=[
                {
                    "id": "asset-1",
                    "type": "image",
                    "title": "角色｜一（图1）",
                    "metadata": {"assetId": "character:一:default"},
                },
                {
                    "id": "asset-2",
                    "type": "image",
                    "title": "角色｜二（图2）",
                    "metadata": {"assetId": "character:二:default"},
                },
            ],
        )
        self.assertTrue(explicit)
        self.assertEqual(
            [ref["nodeId"] for ref in refs],
            ["asset-1", "asset-2"],
        )


class ExplicitReferenceSubmissionTests(unittest.TestCase):
    def setUp(self):
        from test_generation_media_stress import GenerationMediaStressTests

        self.fixture = GenerationMediaStressTests('test_import_asset_reference_pipeline_stress_without_provider_calls')
        self.fixture.setUp()
        self.context = self.fixture.context
        self.root = self.fixture.context.data_root

    def tearDown(self):
        self.fixture.tearDown()

    def test_video_submission_uses_at_for_actions_and_keeps_linked_slots(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project('explicit-at-submit')
            nodes = [
                {
                    'id': 'chen',
                    'type': 'image',
                    'title': '角色｜陈大强（图4）',
                    'metadata': {
                        'status': 'done',
                        'content': '/uploads/chen.png',
                        'assetId': 'character:陈大强:default',
                        'params': {'mediaVersions': [{'url': '/uploads/chen.png'}]},
                    },
                },
                {
                    'id': 'jia',
                    'type': 'image',
                    'title': '角色｜贾富贵（图5）',
                    'metadata': {
                        'status': 'done',
                        'content': '/uploads/jia.png',
                        'assetId': 'character:贾富贵:default',
                        'params': {'mediaVersions': [{'url': '/uploads/jia.png'}]},
                    },
                },
                {
                    'id': 'voice-chen',
                    'type': 'audio',
                    'title': '音频｜陈大强（图4）',
                    'metadata': {
                        'status': 'done',
                        'content': '/uploads/chen.mp3',
                        'assetId': 'voice:陈大强:default',
                        'params': {'mediaVersions': [{'url': '/uploads/chen.mp3'}]},
                    },
                },
                {
                    'id': 'video',
                    'type': 'video',
                    'title': '段3 · 显式引用',
                    'metadata': {
                        'status': 'generating',
                        'prompt': (
                            '@角色｜陈大强（图4）推门进入。'
                            '@音频｜陈大强（图4）说："走。"'
                        ),
                        'params': {
                            'provider': 'mock',
                            'model': 'mock-video',
                            'refs': [
                                {'nodeId': 'chen', 'label': '角色｜陈大强（图4）'},
                                {'nodeId': 'jia', 'label': '角色｜贾富贵（图5）'},
                            ],
                            'voiceRefs': [
                                {'nodeId': 'voice-chen', 'label': '音频｜陈大强（图4）'},
                            ],
                        },
                    },
                },
            ]
            canvas_store.insert_project_nodes(
                project_id,
                nodes,
                episode_id='EP001',
            )
            stored = canvas_store.get_project(project_id)
            project = {
                'id': project_id,
                'payload': stored['payload'],
            }
            video = next(
                item for item in project['payload']['nodes']
                if item['id'] == 'video'
            )
            upload_dir = self.root / 'uploads-check'
            upload_dir.mkdir()
            for name in ('chen.png', 'jia.png', 'chen.mp3'):
                (upload_dir / name).write_bytes(b'x')
            captured = {}

            def fake_generate(*args, **kwargs):
                captured['refs'] = args[4]
                captured['voices'] = args[5]
                captured['params'] = args[3]
                return {
                    'url': 'https://provider.example/result.mp4',
                    'taskId': 'provider-task',
                }

            handler = object.__new__(server.Handler)
            sent = []
            handler._send = lambda status, payload: sent.append((status, payload)) or payload
            provider = {'id': 'mock', 'api_key': 'test-key'}
            model = {
                'id': 'mock-video',
                'remoteModel': 'mock-video',
                'defaultParams': {},
                'limits': {},
            }
            with (
                patch.object(server, '_upload_dir', return_value=str(upload_dir)),
                patch.object(server.provider_catalog, 'resolve_model', return_value=(provider, model, {})),
                patch.object(server, 'ensure_provider_auth'),
                patch.object(server.provider_client, 'generate_video', side_effect=fake_generate),
                patch.object(server, 'persist_generated_media', return_value='/uploads/result.mp4'),
                patch.object(server, 'ensure_video_thumbnail', return_value=''),
                patch.object(server, 'validate_generated_video', return_value={}),
                patch.object(server, 'queue_generated_video_finalize'),
                patch.object(server, 'commit_generation_terminal', return_value={'committed': True}),
            ):
                handler._generate_once(project_id, project, video)

        self.assertIn('refs', captured, sent)
        self.assertEqual(
            [ref['label'] for ref in captured['refs']],
            ['角色｜陈大强（图4）', '角色｜贾富贵（图5）'],
        )
        self.assertEqual([voice['label'] for voice in captured['voices']], ['音频｜陈大强（图4）'])
        self.assertEqual(
            captured['params']['_mentionIdentities'],
            {
                '角色｜陈大强（图4）': 'assetId:character:陈大强:default',
                '音频｜陈大强（图4）': 'assetId:voice:陈大强:default',
            },
        )

    def test_unresolved_at_prevents_provider_call(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project('explicit-at-invalid')
            video = {
                'id': 'video-invalid',
                'type': 'video',
                'title': '段4',
                'metadata': {
                    'status': 'generating',
                    'prompt': '@不存在的角色走进画面。',
                    'params': {'provider': 'mock', 'model': 'mock-video', 'refs': []},
                },
            }
            canvas_store.insert_project_nodes(project_id, [video], episode_id='EP001')
            stored = canvas_store.get_project(project_id)
            project = {'id': project_id, 'payload': stored['payload']}
            video = project['payload']['nodes'][0]
            handler = object.__new__(server.Handler)
            sent = []
            handler._send = lambda status, payload: sent.append((status, payload)) or payload
            provider = {'id': 'mock', 'api_key': 'test-key'}
            model = {
                'id': 'mock-video',
                'remoteModel': 'mock-video',
                'defaultParams': {},
                'limits': {},
            }
            with (
                patch.object(server.provider_catalog, 'resolve_model', return_value=(provider, model, {})),
                patch.object(server, 'ensure_provider_auth'),
                patch.object(server.provider_client, 'generate_video') as generate,
            ):
                handler._generate_once(project_id, project, video)

        generate.assert_not_called()
        self.assertEqual(sent[-1][0], 400)
        self.assertIn('@不存在的角色', sent[-1][1]['error'])

    def test_video_recipe_is_compiled_before_provider_submission(self):
        recipe = recipe_contract.get_recipe('handoff-ensemble-focus')
        slots = {slot['key']: '已填写' for slot in recipe['slots']}
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project('recipe-submit')
            video = {
                'id': 'video-recipe',
                'type': 'video',
                'title': '段1 · 配方',
                'metadata': {
                    'status': 'generating',
                    'prompt': '贾富贵走进画面。',
                    'params': {
                        'provider': 'mock',
                        'model': 'doubao-seedance-2-0',
                        'refs': [],
                        'recipe': {
                            'id': recipe['id'],
                            'version': recipe['version'],
                            'sha256': recipe['contentSha256'],
                            'role': 'primary',
                            'segmentNo': 1,
                            'slots': slots,
                        },
                    },
                },
            }
            canvas_store.insert_project_nodes(project_id, [video], episode_id='EP001')
            stored = canvas_store.get_project(project_id)
            project = {'id': project_id, 'payload': stored['payload']}
            video = project['payload']['nodes'][0]
            captured = {}

            def fake_generate(*args, **kwargs):
                captured['prompt'] = args[2]
                return {
                    'url': 'https://provider.example/recipe.mp4',
                    'taskId': 'provider-recipe-task',
                }

            handler = object.__new__(server.Handler)
            sent = []
            handler._send = lambda status, payload: sent.append((status, payload)) or payload
            provider = {'id': 'mock', 'api_key': 'test-key'}
            model = {
                'id': 'doubao-seedance-2-0',
                'remoteModel': 'doubao-seedance-2-0',
                'defaultParams': {},
                'limits': {},
            }
            with (
                patch.object(server.provider_catalog, 'resolve_model', return_value=(provider, model, {})),
                patch.object(server, 'ensure_provider_auth'),
                patch.object(server.provider_client, 'generate_video', side_effect=fake_generate),
                patch.object(server, 'persist_generated_media', return_value='/uploads/recipe.mp4'),
                patch.object(server, 'ensure_video_thumbnail', return_value=''),
                patch.object(server, 'validate_generated_video', return_value={}),
                patch.object(server, 'queue_generated_video_finalize'),
                patch.object(server, 'commit_generation_terminal', return_value={'committed': True}),
            ):
                handler._generate_once(project_id, project, video)

        self.assertIn('【镜头配方执行锁】', captured['prompt'])
        self.assertIn('配方ID：handoff-ensemble-focus', captured['prompt'])
        self.assertEqual(sent[-1][0], 200)

    def test_missing_recipe_slots_prevent_provider_submission(self):
        recipe = recipe_contract.get_recipe('handoff-ensemble-focus')
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project('recipe-missing-slots')
            video = {
                'id': 'video-recipe-missing',
                'type': 'video',
                'title': '段1 · 配方缺槽',
                'metadata': {
                    'status': 'generating',
                    'prompt': '贾富贵走进画面。',
                    'params': {
                        'provider': 'mock',
                        'model': 'doubao-seedance-2-0',
                        'refs': [],
                        'recipe': {
                            'id': recipe['id'],
                            'version': recipe['version'],
                            'sha256': recipe['contentSha256'],
                            'role': 'primary',
                            'segmentNo': 1,
                            'slots': {},
                        },
                    },
                },
            }
            canvas_store.insert_project_nodes(project_id, [video], episode_id='EP001')
            stored = canvas_store.get_project(project_id)
            project = {'id': project_id, 'payload': stored['payload']}
            video = project['payload']['nodes'][0]
            handler = object.__new__(server.Handler)
            sent = []
            handler._send = lambda status, payload: sent.append((status, payload)) or payload
            provider = {'id': 'mock', 'api_key': 'test-key'}
            model = {
                'id': 'doubao-seedance-2-0',
                'remoteModel': 'doubao-seedance-2-0',
                'defaultParams': {},
                'limits': {},
            }
            with (
                patch.object(server.provider_catalog, 'resolve_model', return_value=(provider, model, {})),
                patch.object(server, 'ensure_provider_auth'),
                patch.object(server.provider_client, 'generate_video') as generate,
            ):
                handler._generate_once(project_id, project, video)

        generate.assert_not_called()
        self.assertEqual(sent[-1][0], 400)
        self.assertIn('RECIPE_REQUIRED_SLOT_MISSING', sent[-1][1]['error'])

    def test_video_submission_reads_linked_background_asset_on_server(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project('server-side-linked-refs')
            jia = {
                'id': 'jia-linked',
                'type': 'image',
                'title': '角色｜贾富贵（图3）',
                'metadata': {
                    'status': 'done',
                    'content': '/uploads/jia-linked.png',
                    'assetId': 'character:jia:linked',
                    'params': {'mediaVersions': [{'url': '/uploads/jia-linked.png'}]},
                },
            }
            accountant = {
                'id': 'accountant-linked',
                'type': 'image',
                'title': '角色｜老会计（图16）',
                'metadata': {
                    'status': 'done',
                    'content': '/uploads/accountant-linked.png',
                    'assetId': 'character:accountant:linked',
                    'params': {'mediaVersions': [{'url': '/uploads/accountant-linked.png'}]},
                },
            }
            video = {
                'id': 'video-linked',
                'type': 'video',
                'title': '段3 · 服务端连线引用',
                'metadata': {
                    'status': 'generating',
                    'prompt': (
                        '画面主体：@角色｜贾富贵（图3）向前走两步。'
                        '老会计只在后景旁观。'
                    ),
                    'params': {
                        'provider': 'mock',
                        'model': 'mock-video',
                        'refs': [
                            {'nodeId': 'jia-linked', 'label': '角色｜贾富贵（图3）'},
                        ],
                    },
                },
            }
            canvas_store.insert_project_nodes(
                project_id,
                [jia, accountant, video],
                episode_id='EP001',
            )
            stored = canvas_store.get_project(project_id)
            stored['payload']['connections'] = [
                {
                    'id': 'edge-background',
                    'fromNodeId': 'accountant-linked',
                    'toNodeId': 'video-linked',
                },
            ]
            canvas_store.update_project(
                project_id,
                payload=stored['payload'],
            )
            project = canvas_store.get_project(project_id)
            project = {'id': project_id, 'payload': project['payload']}
            video_node = next(
                item for item in project['payload']['nodes']
                if item['id'] == 'video-linked'
            )
            upload_dir = self.root / 'uploads-linked'
            upload_dir.mkdir()
            for name in ('jia-linked.png', 'accountant-linked.png'):
                (upload_dir / name).write_bytes(b'x')
            captured = {}

            def fake_generate(*args, **kwargs):
                captured['refs'] = args[4]
                return {
                    'url': 'https://provider.example/linked.mp4',
                    'taskId': 'provider-linked',
                }

            handler = object.__new__(server.Handler)
            sent = []
            handler._send = lambda status, payload: sent.append((status, payload)) or payload
            provider = {'id': 'mock', 'api_key': 'test-key'}
            model = {
                'id': 'mock-video',
                'remoteModel': 'mock-video',
                'defaultParams': {},
                'limits': {},
            }
            with (
                patch.object(server, '_upload_dir', return_value=str(upload_dir)),
                patch.object(server.provider_catalog, 'resolve_model', return_value=(provider, model, {})),
                patch.object(server, 'ensure_provider_auth'),
                patch.object(server.provider_client, 'generate_video', side_effect=fake_generate),
                patch.object(server, 'persist_generated_media', return_value='/uploads/linked.mp4'),
                patch.object(server, 'ensure_video_thumbnail', return_value=''),
                patch.object(server, 'validate_generated_video', return_value={}),
                patch.object(server, 'queue_generated_video_finalize'),
                patch.object(server, 'commit_generation_terminal', return_value={'committed': True}),
            ):
                handler._generate_once(
                    project_id,
                    project,
                    video_node,
                )

        self.assertIn('refs', captured, sent)
        self.assertEqual(
            [ref['label'] for ref in captured['refs']],
            ['角色｜贾富贵（图3）', '角色｜老会计（图16）'],
        )
        self.assertEqual(
            [ref['assetId'] for ref in captured['refs']],
            ['character:jia:linked', 'character:accountant:linked'],
        )


if __name__ == '__main__':
    unittest.main()
