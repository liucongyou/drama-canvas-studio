import unittest

from explicit_references import resolve_explicit_references


def image(node_id, title):
    return {
        'id': node_id,
        'type': 'image',
        'title': title,
        'metadata': {'assetId': f'asset:{node_id}', 'content': f'/uploads/{node_id}.png'},
    }


class ExplicitReferenceTests(unittest.TestCase):
    def test_only_the_at_mentioned_asset_is_returned(self):
        result = resolve_explicit_references(
            prompt='@角色｜陈大强（图4）走进车间，贾富贵只在台词里。',
            nodes=[
                image('chen', '角色｜陈大强（图4）'),
                image('jia', '角色｜贾富贵（图5）'),
            ],
            candidate_refs=[
                {'nodeId': 'chen'},
                {'nodeId': 'jia'},
            ],
            candidate_voices=[],
        )
        self.assertEqual(result.node_ids, ['chen'])
        self.assertEqual(result.refs[0]['assetId'], 'asset:chen')
        self.assertEqual(result.issues, [])

    def test_plain_names_and_dialogue_are_not_references(self):
        result = resolve_explicit_references(
            prompt='【台词】陈大强（急）："别走。" 贾富贵站在门外。',
            nodes=[
                image('chen', '角色｜陈大强（图4）'),
                image('jia', '角色｜贾富贵（图5）'),
            ],
            candidate_refs=[{'nodeId': 'chen'}, {'nodeId': 'jia'}],
            candidate_voices=[],
        )
        self.assertEqual(result.node_ids, [])
        self.assertEqual(result.refs, [])
        self.assertEqual(result.issues, [])

    def test_audio_mention_is_routed_to_voices(self):
        nodes = [
            image('chen', '角色｜陈大强（图4）'),
            {
                'id': 'voice-chen',
                'type': 'audio',
                'title': '音频｜陈大强（图4）',
                'metadata': {'assetId': 'voice:chen', 'content': '/uploads/voice.mp3'},
            },
        ]
        result = resolve_explicit_references(
            prompt='@音频｜陈大强（图4）说：你好。',
            nodes=nodes,
            candidate_refs=[],
            candidate_voices=[{'nodeId': 'voice-chen'}],
        )
        self.assertEqual(result.refs, [])
        self.assertEqual([item['nodeId'] for item in result.voices], ['voice-chen'])

    def test_angle_bracket_mapping_is_not_a_reference(self):
        result = resolve_explicit_references(
            prompt='<@角色｜陈大强（图4）> 是指代标签。',
            nodes=[image('chen', '角色｜陈大强（图4）')],
            candidate_refs=[{'nodeId': 'chen'}],
            candidate_voices=[],
        )
        self.assertEqual(result.node_ids, [])

    def test_unknown_at_token_blocks_submission(self):
        result = resolve_explicit_references(
            prompt='@不存在角色走进画面。',
            nodes=[image('chen', '角色｜陈大强（图4）')],
            candidate_refs=[],
            candidate_voices=[],
        )
        self.assertEqual(result.issues[0].code, 'INVALID_MENTION')
        self.assertIn('@不存在角色', result.issues[0].message)

    def test_duplicate_titles_block_generation_instead_of_guessing(self):
        result = resolve_explicit_references(
            prompt='@角色｜陈大强（图4）走进画面。',
            nodes=[
                image('chen-a', '角色｜陈大强（图4）'),
                image('chen-b', '角色｜陈大强（图4）'),
            ],
            candidate_refs=[],
            candidate_voices=[],
        )
        self.assertEqual(result.refs, [])
        self.assertEqual(result.issues[0].code, 'AMBIGUOUS_MENTION')

    def test_repeated_mention_is_deduplicated_in_first_order(self):
        result = resolve_explicit_references(
            prompt='@角色｜陈大强（图4）转身，@角色｜陈大强（图4）再说一句。',
            nodes=[image('chen', '角色｜陈大强（图4）')],
            candidate_refs=[],
            candidate_voices=[],
        )
        self.assertEqual(result.node_ids, ['chen'])

    def test_identity_map_keeps_asset_identity_when_reference_order_changes(self):
        result = resolve_explicit_references(
            prompt='@角色｜贾富贵（图3）向前走两步。@角色｜老会计（图16）在后景旁观。',
            nodes=[
                image('jia', '角色｜贾富贵（图3）'),
                image('accountant', '角色｜老会计（图16）'),
            ],
            candidate_refs=[
                {'nodeId': 'accountant'},
                {'nodeId': 'jia'},
            ],
            candidate_voices=[],
        )

        self.assertEqual(
            result.reference_identity_map,
            {
                '角色｜贾富贵（图3）': 'assetId:asset:jia',
                '角色｜老会计（图16）': 'assetId:asset:accountant',
            },
        )

    def test_known_mention_followed_by_prose_is_not_reported_invalid(self):
        result = resolve_explicit_references(
            prompt='【场景资产】@道具｜军绿色自行车（图40），支在画左院墙边，本段不移动。',
            nodes=[image('bike', '道具｜军绿色自行车（图40）')],
            candidate_refs=[{'nodeId': 'bike'}],
            candidate_voices=[],
        )

        self.assertEqual(result.issues, [])
        self.assertEqual([ref['nodeId'] for ref in result.refs], ['bike'])


if __name__ == '__main__':
    unittest.main()
