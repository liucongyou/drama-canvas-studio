from __future__ import annotations

import unittest
from unittest.mock import patch

import newapi_video_contract
import provider_catalog


class NewApiVideoContractTests(unittest.TestCase):
    def _models(self):
        for name, entry in provider_catalog._model_profile_catalog().items():
            if isinstance(entry, dict) and entry.get("kind") == "video":
                yield name, entry

    def _sample_params(self, contract, *, with_references=False):
        return {
            "duration": contract["durations"][0],
            "resolution": contract["resolutions"][0],
            "ratio": contract["aspectRatios"][0],
            "mode": "auto",
        }

    def test_every_documented_video_model_compiles_to_its_route(self):
        templates = provider_catalog._documented_video_profile_templates()
        compiled_models = set()
        errors = []
        for name, entry in self._models():
            profile = str(entry.get("profile") or "")
            contract = provider_catalog.documented_video_contract(name)
            template = templates.get(profile)
            if not contract or not template:
                errors.append(f"{name}: missing contract/template")
                continue
            params = self._sample_params(contract)
            modes = contract.get("modes") or ["text2video"]
            images = (
                ["https://example.com/reference.png"]
                if modes == ["image2video"]
                else []
            )
            try:
                compiled = newapi_video_contract.compile_video_request(
                    model={"remoteModel": name, "profile": profile},
                    contract=contract,
                    template=template,
                    prompt="通用接口编译审计",
                    params=params,
                    image_urls=images,
                    video_urls=[],
                    audio_urls=[],
                )
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            self.assertEqual(compiled["path"], template["submitPath"])
            self.assertEqual(compiled["body"]["model"], name)
            compiled_models.add(name)
        self.assertEqual(errors, [])
        self.assertEqual(len(compiled_models), 158)
        self.assertEqual(
            set(templates),
            {"suanli-video", "aistars-video", "hc-video", "openai-video"},
        )

    def test_route_templates_use_documented_fields(self):
        templates = provider_catalog._documented_video_profile_templates()
        self.assertEqual(templates["suanli-video"]["videoField"], "video_refs")
        self.assertEqual(templates["suanli-video"]["audioField"], "audio_refs")
        self.assertEqual(templates["aistars-video"]["videoField"], "videos")
        self.assertEqual(templates["aistars-video"]["audioField"], "audios")
        self.assertEqual(templates["hc-video"]["durationField"], "duration")
        self.assertEqual(templates["hc-video"]["promptField"], "content")

    def test_all_routes_reject_a_duration_outside_the_model_contract(self):
        templates = provider_catalog._documented_video_profile_templates()
        checked = 0
        for name, entry in self._models():
            contract = provider_catalog.documented_video_contract(name)
            template = templates[str(entry.get("profile") or "")]
            invalid = (
                max(contract["durations"]) + 1
                if contract["durations"]
                else 999
            )
            params = self._sample_params(contract)
            params["duration"] = invalid
            with self.subTest(model=name):
                with self.assertRaises(ValueError):
                    newapi_video_contract.compile_video_request(
                        model={"remoteModel": name, "profile": contract["profile"]},
                        contract=contract,
                        template=template,
                        prompt="invalid duration audit",
                        params=params,
                        image_urls=[],
                        video_urls=[],
                        audio_urls=[],
                    )
            checked += 1
        self.assertEqual(checked, 158)

    def test_query_paths_and_status_words_are_template_driven(self):
        templates = provider_catalog._documented_video_profile_templates()
        self.assertEqual(
            newapi_video_contract.build_query_path(
                templates["suanli-video"],
                "task/1",
            ),
            "/suanli/v1/videos/task%2F1",
        )
        self.assertEqual(
            newapi_video_contract.build_query_path(
                templates["aistars-video"],
                "task-1",
            ),
            "/aistars/v1/tasks/task-1",
        )
        self.assertEqual(
            newapi_video_contract.build_query_path(
                templates["hc-video"],
                "task-1",
            ),
            "/hc-cn/v1/video/tasks/task-1",
        )
        self.assertEqual(
            newapi_video_contract.task_id_from_response(
                {"task_id": "task-1"},
                templates["hc-video"]["taskFields"],
            ),
            "task-1",
        )

    def test_saved_video_limits_are_migrated_from_the_exact_contract(self):
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "capability": "video",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "defaultParams": {"ratio": "9:16", "resolution": "480P", "duration": 5},
            "params": {},
            "limits": {},
        }
        contract = provider_catalog.documented_video_contract(
            model["remoteModel"]
        )
        provider_catalog._apply_documented_video_contract(model, contract)
        self.assertEqual(model["defaultParams"]["duration"], 12)
        self.assertEqual(model["defaultParams"]["resolution"], "720P")
        limits = contract["maxReferences"]
        self.assertEqual(limits["images"], 9)
        self.assertEqual(limits["audios"], 3)
        self.assertEqual(limits["videos"], 0)

    def test_hc_duration_default_is_not_assumed_from_sorted_values(self):
        name = "官转doubao-seedance-2.5"
        contract = provider_catalog.documented_video_contract(name)
        model = {
            "id": "official-seedance-25",
            "remoteModel": name,
            "profile": contract["profile"],
            "defaultParams": {"ratio": "adaptive", "resolution": "720P", "duration": 30},
            "params": {},
        }
        provider_catalog._apply_documented_video_contract(model, contract)
        self.assertEqual(contract["durationDefault"], -1)
        self.assertEqual(model["defaultParams"]["duration"], 30)
        compiled = newapi_video_contract.compile_video_request(
            model={"remoteModel": name, "profile": contract["profile"]},
            contract=contract,
            template=provider_catalog._documented_video_profile_templates()["hc-video"],
            prompt="duration default audit",
            params={"ratio": "adaptive", "resolution": "720P"},
            image_urls=[],
            video_urls=[],
            audio_urls=[],
        )
        self.assertEqual(compiled["duration"], -1)


if __name__ == "__main__":
    unittest.main()
