import json
from typing import Any
from config.logging_config import get_case_debug_logger
from tool.toon_value import _encode_toon_value

class DevicesProfile:
    def __init__(self, profile: list | None = None):
        self._env_profile: list[dict] = [d for d in (profile or []) if isinstance(d, dict)]
        self._abstract_device: list[dict] = []

    @staticmethod
    def _normalize_abstract_device_knowledge(knowledge: Any) -> list[dict]:
        normalized: list[dict] = []
        if not isinstance(knowledge, list):
            return normalized

        for device in knowledge:
            if not isinstance(device, dict):
                continue
            dtype = (device.get("type") or "").strip()
            if not dtype:
                continue

            capabilities: list[dict] = []
            seen_caps: set[tuple[str, str]] = set()
            for cap in (device.get("capabilities") or []):
                if not isinstance(cap, dict):
                    continue
                name = (cap.get("name") or "").strip()
                ctype = (cap.get("capabilityType") or "").strip().lower()
                if not name or ctype not in ("trigger", "action"):
                    continue
                cap_key = (name, ctype)
                if cap_key in seen_caps:
                    continue
                seen_caps.add(cap_key)
                desc = cap.get("description", "")
                capabilities.append(
                    {
                        "name": name,
                        "description": desc if isinstance(desc, str) else "",
                        "capabilityType": ctype,
                    }
                )

            sources: list[dict] = []
            seen_sources: set[tuple[str, str, str, str]] = set()
            for src in (device.get("sources") or []):
                if not isinstance(src, dict):
                    continue
                dev_name = (src.get("device_name") or "").strip()
                dev_type = (src.get("device_type") or "").strip()
                cap_name = (src.get("capability_name") or "").strip()
                cap_type = (src.get("capabilityType") or "").strip().lower()
                if not dev_name or not cap_name or cap_type not in ("trigger", "action"):
                    continue
                src_key = (dev_name, dev_type, cap_name, cap_type)
                if src_key in seen_sources:
                    continue
                seen_sources.add(src_key)
                cap_desc = src.get("capability_description", "")
                sources.append(
                    {
                        "device_name": dev_name,
                        "device_type": dev_type,
                        "capability_name": cap_name,
                        "capabilityType": cap_type,
                        "capability_description": cap_desc if isinstance(cap_desc, str) else "",
                    }
                )

            desc = device.get("description", "")
            normalized.append(
                {
                    "type": dtype,
                    "description": desc if isinstance(desc, str) else "",
                    "capabilities": capabilities,
                    "sources": sources,
                }
            )

        return normalized


    @staticmethod
    def _is_targeted_shutdown_device(dev: str) -> bool:
        text = (dev or "").strip()
        if not text:
            return False
        return (
            "所有影音、照明、温控及厨电类电器" in text
            or ("影音" in text and "电器" in text)
            or ("照明" in text and "电器" in text)
            or ("温控" in text and "电器" in text)
            or ("厨电" in text and "电器" in text)
        )

    @staticmethod
    def _matches_targeted_shutdown_category(
        name: str,
        dtype: str,
        description: str,
        capabilities: list[dict] | None = None,
    ) -> bool:
        caps = [c for c in (capabilities or []) if isinstance(c, dict)]
        text = " ".join(
            [
                name or "",
                dtype or "",
                description or "",
                " ".join(
                    f"{(c.get('name') or '').strip()} {(c.get('description') or '').strip()}"
                    for c in caps
                ),
            ]
        )
        lowered = text.lower()
        excluded_keywords = [
            "door",
            "lock",
            "alarm",
            "smoke",
            "sensor",
            "detector",
            "门",
            "锁",
            "报警",
            "烟雾",
            "感知",
            "传感",
        ]
        if any(keyword in lowered for keyword in excluded_keywords):
            return False

        category_keywords = [
            "television",
            "tv",
            "speaker",
            "audio",
            "soundbar",
            "projector",
            "light",
            "lamp",
            "bulb",
            "lighting",
            "illumination",
            "air_conditioner",
            "conditioner",
            "thermostat",
            "heater",
            "fan",
            "hvac",
            "climate",
            "hood",
            "kitchen",
            "oven",
            "microwave",
            "cooker",
            "stove",
            "dishwasher",
            "fridge",
            "refrigerator",
            "电视",
            "音箱",
            "音响",
            "投影",
            "影音",
            "灯",
            "照明",
            "灯带",
            "灯泡",
            "空调",
            "温控",
            "暖气",
            "地暖",
            "风扇",
            "取暖",
            "油烟机",
            "抽油烟机",
            "厨电",
            "厨房",
            "烤箱",
            "微波炉",
            "灶",
            "冰箱",
            "洗碗机",
            "电饭煲",
            "蒸箱",
        ]
        return any(keyword in lowered for keyword in category_keywords)

    def load_abstract_device_knowledge(self, knowledge: Any, debug: bool = False) -> None:
        self._abstract_device = self._normalize_abstract_device_knowledge(knowledge)
        if debug:
            print(f"已加载外部设备知识库，共 {len(self._abstract_device)} 个抽象设备")

    def load_abstract_device_knowledge_from_file(self, file_path: str, debug: bool = False) -> None:
        with open(file_path, "r", encoding="utf-8") as f:
            knowledge = json.load(f)
        self.load_abstract_device_knowledge(knowledge, debug=debug)
        
    def set_env_profile(self, profile: list, debug: bool = False):
        if debug:
            print("正在配置设备环境...")
        self._env_profile = [d for d in (profile or []) if isinstance(d, dict)]
        if debug:
            print(f"设备环境配置完成，共 {len(self._env_profile)} 个设备")
            print("正在引入设备本体知识...")
        try:
            self._abstract_device = self.build_abstract_device_knowledge()
        except Exception as e:
            print(f"Error in build_abstract_device_knowledge: {e}")
            self._abstract_device = []
        if debug:
            print(f"设备本体知识配置完成，共 {len(self._abstract_device)} 个设备")
            print("="*100)
            print(f"设备本体知识: {json.dumps(self._abstract_device, ensure_ascii=False, indent=2)}")
            print("="*100)
            

    @property
    def env_profile(self) -> list:
        return self._env_profile

    def get_available_device_names(self) -> list[str]:
        names: list[str] = []
        for d in self._env_profile:
            n = d.get("name")
            if isinstance(n, str):
                s = n.strip()
                if s and s not in names:
                    names.append(s)
        return names

    def get_abstract_devices(self) -> list[dict]:
        """
        返回当前已构建的抽象设备知识副本，供其他模块做只读匹配。
        """
        if not isinstance(self._abstract_device, list):
            return []
        return json.loads(json.dumps(self._abstract_device, ensure_ascii=False))

    def get_device_profile_context(self, device_name: str) -> dict[str, Any]:
        """
        为大模型提供设备画像上下文：仅做 name 精确匹配。

        匹配成功则返回该设备的完整 dict；匹配失败返回空 dict。
        """
        name = (device_name or "").strip()
        profiles = list(self._env_profile or [])

        for d in profiles:
            if isinstance(d, dict) and (d.get("name") or "").strip() == name:
                return d

        return {}

    def get_all_device_knowledge_str(self) -> str:
        lines = []
        for device in self._env_profile:
            name = device.get('name', 'unknown_device')
            dtype = device.get('type', 'unknown_type')
            caps = device.get('capabilities', [])
            
            lines.append(f"{name}:")
            lines.append(f"  type: {dtype}")
            
            if caps:
                fields = ["name", "description"]
                lines.append(f"  capabilities[{len(caps)}]{{{','.join(fields)}}}:")
                for cap in caps:
                    row = [
                        _encode_toon_value((cap or {}).get('name', '')),
                        _encode_toon_value((cap or {}).get('description', ''))
                    ]
                    lines.append("    " + ",".join(row))
        
        return "\n".join(lines)

    def get_abstract_device_knowledge_str_by_capability_kind(self, kind: str) -> str:
        k = (kind or "").lower().strip()
        lines: list[str] = []

        for device in (self._abstract_device or []):
            if not isinstance(device, dict):
                continue
            dtype = (device.get("type") or "Unknown").strip()
            reps: list[dict] = []
            seen: set[tuple[str, str]] = set()

            for cap in (device.get("capabilities") or []):
                if not isinstance(cap, dict):
                    continue
                cap_type = ((cap.get("capabilityType") or "").lower().strip())
                if k in ("trigger", "action") and cap_type != k:
                    continue
                cap_name = (cap.get("name") or "").strip()
                if not cap_name:
                    continue
                key = (cap_name, cap_type)
                if key in seen:
                    continue
                seen.add(key)
                reps.append(cap)

            if not reps:
                continue

            lines.append(f"{dtype}:")
            fields = ["name", "description"]
            lines.append(f"  capabilities[{len(reps)}]{{{','.join(fields)}}}:")
            for cap in reps:
                row = [
                    _encode_toon_value((cap or {}).get("name", "")),
                    _encode_toon_value((cap or {}).get("description", "")),
                ]
                lines.append("    " + ",".join(row))

        return "\n".join(lines)

    def build_abstract_device_knowledge(self) -> list[dict]:
        from config.ai_client import simple_completion, create_system_message, create_user_message

        items: list[dict] = []
        for device in self._env_profile:
            dev_name = device.get("name", "")
            dev_type = device.get("type", "")
            for cap in (device.get("capabilities") or []):
                if not isinstance(cap, dict):
                    continue
                cap_type = (cap.get("capabilityType") or "").lower().strip()
                cap_name = cap.get("name", "") or ""
                if cap_type not in ("trigger", "action"):
                    cap_type = "trigger" if "reading" in cap_name else "action"
                items.append(
                    {
                        "device_name": dev_name,
                        "device_type": dev_type,
                        "capability_name": cap_name,
                        "capability_description": cap.get("description", ""),
                        "capabilityType": cap_type,
                    }
                )

        # print(f"abstract_build.items_len: {len(items)}")
        # print(f"abstract_build.items: {json.dumps(items, ensure_ascii=False, indent=2)}")
        if not items:
            self._abstract_device = []
            return self._abstract_device

        rewrite_system = (
            "你是智能家居设备能力抽象专家。你的任务是把“具体设备+具体能力”改写成“抽象功能设备+抽象功能能力”。\n"
            "输入输出格式：\n"
            "输入严格 JSON：{\"inputs\": [ ... ]}，其中每个 inputs[i] 包含字段：\n"
            "- device_name: 具体设备实例名\n"
            "- device_type: 具体设备类型\n"
            "- capability_name: 具体能力名\n"
            "- capability_description: 具体能力描述\n"
            "- capabilityType: trigger 或 action\n"
            "输出严格 JSON：{\"results\": [ ... ]}，并且 results 长度必须与 inputs 一致，且第 i 个结果对应第 i 个输入。\n"
            "每个 results[i] 必须包含字段：\n"
            "- device_name: 抽象功能设备名称（中文短语）\n"
            "- capability_name: 抽象功能能力名称（中文动词短语，带括号）\n"
            "- capabilityType: trigger 或 action\n"
            "可选字段：capability_description\n"
            "示例：\n"
            "输入：{\"inputs\":[{\"device_name\":\"空调1\",\"device_type\":\"air_conditioner\",\"capability_name\":\"reading\",\"capability_description\":\"读取运行状态\",\"capabilityType\":\"trigger\"}]}\n"
            "输出：{\"results\":[{\"device_name\":\"空气温度制冷设备\",\"capability_name\":\"获取状态()\",\"capability_description\":\"获取制冷/制热/关闭状态\",\"capabilityType\":\"trigger\"}]}\n"
            "要求：\n"
            "1) 抽象功能设备名称用中文短语，尽量表达单一语义功能（常见有：空气温度制冷设备、空气温度制热设备、温度感知设备、报警设备、烟雾感知设备）。不允许直接使用设备类型（如空调、温度感知器），因为这个抽象粒度太低了。\n"
            "2) 抽象功能能力名称用中文动词短语，要带括号（如：打开()、关闭()、获取读数()、打开报警()）。\n"
            "3) capabilityType 必须为 trigger 或 action，并与输入语义一致。\n"
            "4) 输出严格 JSON，并且 results 长度必须与 inputs 一致。\n"
            "5) 如果输入本身已经较抽象，也要保持语义一致。\n"
            "特别注意：humidifier和dehumidifier是不一样的抽象设备。humidifier应该抽象为空气湿度增加设备，dehumidifier应该抽象为空气湿度减少设备。\n"
            "特别注意：像人在传感器等感知设备，优先抽象为'人体感知设备'"
        )
        rewrite_user = json.dumps({"inputs": items}, ensure_ascii=False, indent=2)
        rewrite_raw = simple_completion(
            messages_input=[create_system_message(rewrite_system), create_user_message(rewrite_user)],
            use_json=True,
            temperature=0.0
        )
        # print(f"abstract_build.rewrite_raw: {rewrite_raw}")
        try:
            rewrite_parsed = json.loads(rewrite_raw) if isinstance(rewrite_raw, str) else (rewrite_raw or {})
        except Exception:
            rewrite_parsed = {}
        # print(f"abstract_build.rewrite_parsed: {json.dumps(rewrite_parsed, ensure_ascii=False, indent=2)}")
        results = (rewrite_parsed or {}).get("results", [])
        if not isinstance(results, list) or len(results) != len(items):
            results = []
        # print(f"abstract_build.results_len: {len(results)} / expected: {len(items)}")
        # print(f"abstract_build.results: {json.dumps(results, ensure_ascii=False, indent=2)}")

        abstract_map: dict[str, dict] = {}
        skipped_device_name = 0
        skipped_capability_name = 0
        for idx, src in enumerate(items):
            r = results[idx] if idx < len(results) and isinstance(results[idx], dict) else {}
            abs_device = r.get("device_name") or ""
            abs_cap_name = r.get("capability_name") or ""
            abs_cap_desc = r.get("capability_description")
            abs_cap_type = r.get("capabilityType") or src.get("capabilityType") or ""
            if not isinstance(abs_cap_desc, str) or not abs_cap_desc.strip():
                abs_cap_desc = src.get("capability_description", "") or ""

            # print(
            #     json.dumps(
            #         {
            #             "idx": idx,
            #             "src": src,
            #             "result": r,
            #             "picked": {
            #                 "device_name": abs_device,
            #                 "capability_name": abs_cap_name,
            #                 "capabilityType": abs_cap_type,
            #                 "capability_description": abs_cap_desc,
            #             },
            #         },
            #         ensure_ascii=False,
            #         indent=2,
            #     )
            # )
            if not isinstance(abs_device, str) or not abs_device.strip():
                skipped_device_name += 1
                continue
            if not isinstance(abs_cap_name, str) or not abs_cap_name.strip():
                skipped_capability_name += 1
                continue
            if not isinstance(abs_cap_type, str) or abs_cap_type.strip().lower() not in ("trigger", "action"):
                abs_cap_type = src.get("capabilityType") or ""

            dev_entry = abstract_map.setdefault(
                abs_device.strip(),
                {"type": abs_device.strip(), "description": "", "capabilities": [], "sources": []},
            )
            caps = dev_entry["capabilities"]
            sources = dev_entry["sources"]
            cap_key = (abs_cap_name.strip(), (abs_cap_type or "").strip().lower())
            if not any(
                isinstance(c, dict) and (c.get("name"), (c.get("capabilityType") or "").strip().lower()) == cap_key
                for c in caps
            ):
                caps.append(
                    {
                        "name": abs_cap_name.strip(),
                        "description": abs_cap_desc,
                        "capabilityType": cap_key[1],
                    }
                )
            src_dev_name = (src.get("device_name") or "").strip()
            src_dev_type = (src.get("device_type") or "").strip()
            src_cap_name = (src.get("capability_name") or "").strip()
            src_cap_type = (src.get("capabilityType") or "").strip().lower()
            if src_dev_name and src_cap_name:
                src_key = (src_dev_name, src_dev_type, src_cap_name, src_cap_type)
                if not any(
                    isinstance(s, dict)
                    and (
                        (s.get("device_name") or "").strip(),
                        (s.get("device_type") or "").strip(),
                        (s.get("capability_name") or "").strip(),
                        (s.get("capabilityType") or "").strip().lower(),
                    )
                    == src_key
                    for s in sources
                ):
                    sources.append(
                        {
                            "device_name": src_dev_name,
                            "device_type": src_dev_type,
                            "capability_name": src_cap_name,
                            "capabilityType": src_cap_type,
                            "capability_description": src.get("capability_description", "") or "",
                        }
                    )

        devices_for_dedupe: list[dict] = []
        for dtype, entry in abstract_map.items():
            caps = [c for c in (entry.get("capabilities") or []) if isinstance(c, dict)]
            if len(caps) <= 1:
                continue
            devices_for_dedupe.append(
                {
                    "device_type": dtype,
                    "capabilities": [
                        {
                            "name": (c.get("name") or "").strip(),
                            "description": c.get("description", "") or "",
                            "capabilityType": (c.get("capabilityType") or "").strip().lower(),
                        }
                        for c in caps
                    ],
                }
            )

        if devices_for_dedupe:
            dedupe_system = (
                "你是智能家居设备能力去重助手。\n"
                "给定若干功能设备(device_type)及其能力列表(capabilities)，请对每个 device_type 内部做能力合并去重。\n"
                "只在非常确定是同一个功能/明显包含关系时才合并；不确定就保留为不同能力。\n"
                "合并规则：\n"
                "1) 只在同一 capabilityType 内合并（trigger 只与 trigger 合并，action 只与 action 合并）。\n"
                "2) 合并后能力的 name 必须从该 device_type 的输入能力 name 中选取一个，优先选择更通用、更短、更标准的表达（例如：打开()、关闭()、获取读数()、获取状态()）。\n"
                "3) 不要发明新的能力名；不要删除有明显不同语义的能力。\n"
                "4) 输出严格 JSON：{\"results\": [{\"device_type\": \"...\", \"capabilities\": [{\"name\": \"...\", \"description\": \"...\", \"capabilityType\": \"trigger|action\"}, ...]} , ...]}。\n"
                "5) results 可以只包含输入中的部分 device_type，但包含的 device_type 必须原样返回。\n"
            )

            def _dedupe_one_batch(batch: list[dict]) -> dict[str, list[dict]]:
                raw = simple_completion(
                    messages_input=[
                        create_system_message(dedupe_system),
                        create_user_message(json.dumps({"devices": batch}, ensure_ascii=False, indent=2)),
                    ],
                    use_json=True,
                    temperature=0.0
                )
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    parsed = {}
                results = (parsed or {}).get("results") or []
                if not isinstance(results, list):
                    return {}
                out: dict[str, list[dict]] = {}
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    dt = (r.get("device_type") or "").strip()
                    caps = r.get("capabilities") or []
                    if not dt or not isinstance(caps, list):
                        continue
                    out[dt] = caps
                return out

            deduped_map: dict[str, list[dict]] = {}
            batch: list[dict] = []
            batch_len = 0
            for d in devices_for_dedupe:
                s = json.dumps(d, ensure_ascii=False)
                if batch and batch_len + len(s) > 12000:
                    deduped_map.update(_dedupe_one_batch(batch))
                    batch = []
                    batch_len = 0
                batch.append(d)
                batch_len += len(s)
            if batch:
                deduped_map.update(_dedupe_one_batch(batch))

            for dtype, new_caps in deduped_map.items():
                if dtype not in abstract_map or not isinstance(new_caps, list):
                    continue
                prev_caps = [c for c in (abstract_map[dtype].get("capabilities") or []) if isinstance(c, dict)]
                best_desc: dict[tuple[str, str], str] = {}
                for c in prev_caps:
                    n = (c.get("name") or "").strip()
                    t = (c.get("capabilityType") or "").strip().lower()
                    if not n or t not in ("trigger", "action"):
                        continue
                    d = c.get("description", "")
                    if not isinstance(d, str):
                        d = ""
                    key = (n, t)
                    if key not in best_desc or len(d.strip()) > len((best_desc.get(key) or "").strip()):
                        best_desc[key] = d

                cleaned: list[dict] = []
                seen: set[tuple[str, str]] = set()
                for c in new_caps:
                    if not isinstance(c, dict):
                        continue
                    name = (c.get("name") or "").strip()
                    ctype = (c.get("capabilityType") or "").strip().lower()
                    if not name or ctype not in ("trigger", "action"):
                        continue
                    desc = c.get("description", "")
                    if not isinstance(desc, str):
                        desc = ""
                    if not desc.strip():
                        desc = best_desc.get((name, ctype), "") or ""
                    key = (name, ctype)
                    if key in seen:
                        continue
                    seen.add(key)
                    cleaned.append({"name": name, "description": desc, "capabilityType": ctype})
                if cleaned:
                    abstract_map[dtype]["capabilities"] = cleaned

        # print(
        #     json.dumps(
        #         {
        #             "abstract_build.skipped": {
        #                 "missing_device_name": skipped_device_name,
        #                 "missing_capability_name": skipped_capability_name,
        #             }
        #         },
        #         ensure_ascii=False,
        #         indent=2,
        #     )
        # )
        desc_inputs: list[dict] = []
        for dtype in sorted(abstract_map.keys()):
            desc_inputs.append(
                {
                    "device_type": dtype,
                    "capabilities": [
                        {
                            "name": c.get("name", ""),
                            "description": c.get("description", ""),
                            "capabilityType": c.get("capabilityType", ""),
                        }
                        for c in (abstract_map[dtype].get("capabilities") or [])
                        if isinstance(c, dict)
                    ],
                }
            )

        # print(f"abstract_build.abstract_types: {sorted(abstract_map.keys())}")
        desc_system = (
            "你是智能家居功能设备命名与说明撰写助手。根据每个功能设备的能力列表，为其写一句中文描述。\n"
            "要求：\n"
            "1) 描述要简洁（20字以内优先），体现该设备能做什么。\n"
            "2) 输出严格 JSON：{ \"device_descriptions\": { \"<device_type>\": \"<desc>\" } }\n"
        )
        desc_user = json.dumps({"devices": desc_inputs}, ensure_ascii=False, indent=2)
        desc_raw = simple_completion(
            messages_input=[create_system_message(desc_system), create_user_message(desc_user)],
            use_json=True,
            temperature=0.2
        )
        # print(f"abstract_build.desc_raw: {desc_raw}")
        try:
            desc_parsed = json.loads(desc_raw) if isinstance(desc_raw, str) else (desc_raw or {})
        except Exception:
            desc_parsed = {}
        # print(f"abstract_build.desc_parsed: {json.dumps(desc_parsed, ensure_ascii=False, indent=2)}")
        desc_map = (desc_parsed or {}).get("device_descriptions", {})
        if not isinstance(desc_map, dict):
            desc_map = {}

        for dtype, entry in abstract_map.items():
            d = desc_map.get(dtype, "")
            if isinstance(d, str):
                entry["description"] = d.strip()

        self._abstract_device = [abstract_map[k] for k in sorted(abstract_map.keys())]
        # print(f"abstract_device: {json.dumps(self._abstract_device, ensure_ascii=False, indent=2)}")
        return self._abstract_device

    def map_abstract_device_and_capability(
        self,
        device: str,
        capability: str,
        kind: str | None = None,
        instances: list[str] | None = None,
        value: str | None = None,
        operator: str | None = None,
    ) -> list[dict]:
        from config.ai_client import simple_completion, create_system_message, create_user_message

        dev = (device or "").strip()
        cap = (capability or "").strip()
        debug_logger = get_case_debug_logger()

        def _sample_rows(rows: list[Any] | None, limit: int = 5):
            if not rows:
                return []
            return rows[:limit]

        def _log_stage(stage: str, **payload: Any) -> None:
            try:
                debug_logger.info(
                    f"[DeviceMap] {stage}: "
                    + json.dumps(payload, ensure_ascii=False, default=str)
                )
            except Exception:
                pass

        _log_stage(
            "start",
            device=dev,
            capability=cap,
            kind=kind,
            instances_count=len(instances or []),
            instances_sample=_sample_rows(list(instances or []), limit=10),
            value=value,
            operator=operator,
        )
        if not dev or not cap:
            _log_stage("abort.empty_input", reason="device_or_capability_empty")
            return []
        k = (kind or "").lower().strip()

        inst_set = set(instances or [])
        candidates: list[dict] = []
        for d in self._env_profile:
            if not isinstance(d, dict):
                continue
            name = (d.get("name") or "").strip()
            if not name:
                continue
            if inst_set and name not in inst_set:
                continue
            caps_src = []
            for c in (d.get("capabilities") or []):
                if not isinstance(c, dict):
                    continue
                cap_type = (c.get("capabilityType") or "").lower().strip()
                if k in ("trigger", "action") and cap_type and cap_type != k:
                    continue
                caps_src.append(
                    {
                        "name": c.get("name", ""),
                        "description": c.get("description", ""),
                        "capabilityType": c.get("capabilityType", ""),
                    }
                )
            candidates.append(
                {
                    "name": name,
                    "type": d.get("type", ""),
                    "capabilities": caps_src,
                }
            )

        _log_stage(
            "candidates.built",
            candidate_count=len(candidates),
            candidate_sample=[
                {
                    "name": (c.get("name") or "").strip(),
                    "type": (c.get("type") or "").strip(),
                    "capability_count": len(c.get("capabilities") or []),
                }
                for c in candidates[:10]
            ],
        )
        if not candidates:
            _log_stage("abort.no_candidates", reason="env_candidates_empty_after_instance_and_kind_filter")
            return []

        abs_caps: list[dict] = []
        trace_sources: list[dict] = []
        for ad in (self._abstract_device or []):
            if not isinstance(ad, dict):
                continue
            if (ad.get("type") or "").strip() != dev:
                continue
            for c in (ad.get("capabilities") or []):
                if not isinstance(c, dict):
                    continue
                if (c.get("name") or "").strip() != cap:
                    continue
                abs_caps.append(
                    {
                        "device_type": ad.get("type", ""),
                        "capability_name": c.get("name", ""),
                        "capability_description": c.get("description", ""),
                        "capabilityType": c.get("capabilityType", ""),
                    }
                )
            for s in (ad.get("sources") or []):
                if not isinstance(s, dict):
                    continue
                s_cap = (s.get("capability_name") or "").strip()
                s_type = (s.get("capabilityType") or "").strip().lower()
                if s_cap and s_cap != cap:
                    continue
                if k in ("trigger", "action") and s_type and s_type != k:
                    continue
                trace_sources.append(
                    {
                        "device_name": (s.get("device_name") or "").strip(),
                        "device_type": (s.get("device_type") or "").strip(),
                        "capability_name": s_cap,
                        "capabilityType": s_type,
                        "capability_description": (s.get("capability_description") or "").strip(),
                    }
                )

        _log_stage(
            "abstract.exact_match",
            abstract_capability_count=len(abs_caps),
            abstract_capability_sample=_sample_rows(abs_caps),
            trace_source_count=len(trace_sources),
            trace_source_sample=_sample_rows(trace_sources),
        )
        if not abs_caps:
            abstract_candidates: list[dict] = []
            for ad in (self._abstract_device or []):
                if not isinstance(ad, dict):
                    continue
                caps_list = []
                for c in (ad.get("capabilities") or []):
                    if not isinstance(c, dict):
                        continue
                    caps_list.append(
                        {
                            "name": (c.get("name") or "").strip(),
                            "description": (c.get("description") or "").strip(),
                            "capabilityType": (c.get("capabilityType") or "").strip().lower(),
                        }
                    )
                abstract_candidates.append(
                    {
                        "type": (ad.get("type") or "").strip(),
                        "description": (ad.get("description") or "").strip(),
                        "capabilities": caps_list,
                    }
                )
            select_abs_sys = (
                "你是智能家居抽象设备匹配助手。\n"
                "给定目标抽象设备与抽象能力，从候选抽象设备中选择最相关的抽象设备与能力对，用作近义替代。\n"
                "只输出 JSON，格式为：{\"selected_types\": [\"抽象设备类型\"], \"selected_pairs\": [{\"device_type\": \"抽象设备类型\", \"capability_name\": \"能力名\"}, ...]}。\n"
                "当不确定时，宁可多选一些可能相关的对或类型，但不要发明列表之外的类型名或能力名，并且要过滤掉完全不相关的对或类型。"
            )
            select_abs_user = json.dumps(
                {
                    "target_abstract_device": dev,
                    "target_capability": cap,
                    "kind": k,
                    "candidates": abstract_candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
            _log_stage(
                "abstract.semantic_select.request",
                target_device=dev,
                target_capability=cap,
                kind=k,
                abstract_candidate_count=len(abstract_candidates),
                abstract_candidate_sample=_sample_rows(abstract_candidates),
            )
            try:
                select_abs_raw = simple_completion(
                    messages_input=[
                        create_system_message(select_abs_sys),
                        create_user_message(select_abs_user),
                    ],
                    use_json=True,
                    temperature=0.0,
                )
                select_abs_parsed = json.loads(select_abs_raw) if isinstance(select_abs_raw, str) else (select_abs_raw or {})
            except Exception:
                select_abs_parsed = {}
            selected_pairs = (select_abs_parsed or {}).get("selected_pairs") or []
            selected_types = (select_abs_parsed or {}).get("selected_types") or []
            _log_stage(
                "abstract.semantic_select.response",
                raw=select_abs_raw if 'select_abs_raw' in locals() else None,
                parsed=select_abs_parsed,
                selected_pairs_count=len(selected_pairs) if isinstance(selected_pairs, list) else 0,
                selected_types_count=len(selected_types) if isinstance(selected_types, list) else 0,
            )
            if not isinstance(selected_pairs, list):
                selected_pairs = []
            if not isinstance(selected_types, list):
                selected_types = []
            pair_set = {
                (
                    (p.get("device_type") or "").strip(),
                    (p.get("capability_name") or "").strip(),
                )
                for p in selected_pairs
                if isinstance(p, dict)
                and (p.get("device_type") or "").strip()
                and (p.get("capability_name") or "").strip()
            }
            selected_type_set = {str(x).strip() for x in selected_types if str(x).strip()}
            if pair_set:
                abs_caps = []
                trace_sources = []
                for ad in (self._abstract_device or []):
                    if not isinstance(ad, dict):
                        continue
                    dtype = (ad.get("type") or "").strip()
                    for c in (ad.get("capabilities") or []):
                        if not isinstance(c, dict):
                            continue
                        cname = (c.get("name") or "").strip()
                        if (dtype, cname) not in pair_set:
                            continue
                        abs_caps.append(
                            {
                                "device_type": dtype,
                                "capability_name": c.get("name", ""),
                                "capability_description": c.get("description", ""),
                                "capabilityType": c.get("capabilityType", ""),
                            }
                        )
                _log_stage(
                    "abstract.semantic_select.apply_pairs",
                    pair_count=len(pair_set),
                    abstract_capability_count=len(abs_caps),
                    abstract_capability_sample=_sample_rows(abs_caps),
                )
            elif selected_type_set:
                abs_caps = []
                trace_sources = []
                for ad in (self._abstract_device or []):
                    if not isinstance(ad, dict):
                        continue
                    dtype = (ad.get("type") or "").strip()
                    if dtype not in selected_type_set:
                        continue
                    for c in (ad.get("capabilities") or []):
                        if not isinstance(c, dict):
                            continue
                        abs_caps.append(
                            {
                                "device_type": dtype,
                                "capability_name": c.get("name", ""),
                                "capability_description": c.get("description", ""),
                                "capabilityType": c.get("capabilityType", ""),
                            }
                        )
                    for s in (ad.get("sources") or []):
                        if not isinstance(s, dict):
                            continue
                        s_type = (s.get("capabilityType") or "").strip().lower()
                        if k in ("trigger", "action") and s_type and s_type != k:
                            continue
                        trace_sources.append(
                            {
                                "device_name": (s.get("device_name") or "").strip(),
                                "device_type": (s.get("device_type") or "").strip(),
                                "capability_name": (s.get("capability_name") or "").strip(),
                                "capabilityType": s_type,
                                "capability_description": (s.get("capability_description") or "").strip(),
                            }
                        )
                    for s in (ad.get("sources") or []):
                        if not isinstance(s, dict):
                            continue
                        s_cap = (s.get("capability_name") or "").strip()
                        s_type = (s.get("capabilityType") or "").strip().lower()
                        if (dtype, s_cap) not in pair_set:
                            continue
                        if k in ("trigger", "action") and s_type and s_type != k:
                            continue
                        trace_sources.append(
                            {
                                "device_name": (s.get("device_name") or "").strip(),
                                "device_type": (s.get("device_type") or "").strip(),
                                "capability_name": s_cap,
                                "capabilityType": s_type,
                                "capability_description": (s.get("capability_description") or "").strip(),
                            }
                        )
                _log_stage(
                    "abstract.semantic_select.apply_types",
                    selected_type_count=len(selected_type_set),
                    selected_types=sorted(selected_type_set),
                    abstract_capability_count=len(abs_caps),
                    abstract_capability_sample=_sample_rows(abs_caps),
                    trace_source_count=len(trace_sources),
                    trace_source_sample=_sample_rows(trace_sources),
                )
            else:
                _log_stage(
                    "abstract.semantic_select.empty",
                    reason="llm_selected_no_pairs_or_types",
                )
        if not abs_caps:
            fallback_types: set[str] = set()
            for ad in (self._abstract_device or []):
                if not isinstance(ad, dict):
                    continue
                dtype = (ad.get("type") or "").strip()
                if not dtype:
                    continue
                desc = (ad.get("description") or "").strip()
                caps_list = [c for c in (ad.get("capabilities") or []) if isinstance(c, dict)]
                if self._is_targeted_shutdown_device(dev):
                    if not self._matches_targeted_shutdown_category("", dtype, desc, caps_list):
                        continue
                    if any((c.get("capabilityType") or "").strip().lower() == "action" for c in caps_list):
                        fallback_types.add(dtype)
                elif "人体感知" in dev:
                    if "感知" not in dtype:
                        continue
                    text = desc + " " + " ".join(
                        [
                            (c.get("name") or "").strip() + " " + (c.get("description") or "").strip()
                            for c in caps_list
                        ]
                    )
                    if "人" in text or "运动" in text:
                        fallback_types.add(dtype)
            if fallback_types:
                abs_caps = []
                trace_sources = []
                for ad in (self._abstract_device or []):
                    if not isinstance(ad, dict):
                        continue
                    dtype = (ad.get("type") or "").strip()
                    if dtype not in fallback_types:
                        continue
                    for c in (ad.get("capabilities") or []):
                        if not isinstance(c, dict):
                            continue
                        ctype = (c.get("capabilityType") or "").strip().lower()
                        if k in ("trigger", "action") and ctype and ctype != k:
                            continue
                        abs_caps.append(
                            {
                                "device_type": dtype,
                                "capability_name": c.get("name", ""),
                                "capability_description": c.get("description", ""),
                                "capabilityType": c.get("capabilityType", ""),
                            }
                        )
                    for s in (ad.get("sources") or []):
                        if not isinstance(s, dict):
                            continue
                        s_type = (s.get("capabilityType") or "").strip().lower()
                        if k in ("trigger", "action") and s_type and s_type != k:
                            continue
                        trace_sources.append(
                            {
                                "device_name": (s.get("device_name") or "").strip(),
                                "device_type": (s.get("device_type") or "").strip(),
                                "capability_name": (s.get("capability_name") or "").strip(),
                                "capabilityType": s_type,
                                "capability_description": (s.get("capability_description") or "").strip(),
                            }
                        )
            _log_stage(
                "abstract.fallback",
                fallback_type_count=len(fallback_types),
                fallback_types=sorted(fallback_types),
                abstract_capability_count=len(abs_caps),
                abstract_capability_sample=_sample_rows(abs_caps),
                trace_source_count=len(trace_sources),
                trace_source_sample=_sample_rows(trace_sources),
            )

        select_dev_sys = (
            "你是智能家居设备匹配助手。\n"
            "给定一个抽象功能设备和能力，以及候选的具体设备实例列表，请选择所有可以实现该抽象能力的具体设备。\n"
            "你还会得到 trace_sources，它是该抽象设备能力从具体设备抽象而来的追踪关系，请优先从这些设备中选择，并结合设备名称语义与能力描述进行判断。\n"
            "如果 trace_sources 指向的设备看起来可实现能力，即便其类型名像传感器，也允许选中。\n"
            "只输出 JSON，格式为：{\"selected_devices\": [\"实例名1\", \"实例名2\", ...]}。\n"
            "当不确定时，宁可多选一些可能相关的设备，但不要发明列表之外的实例名。"
            "特别注意：当抽象设备是“所有影音、照明、温控及厨电类电器”时，只选择影音、照明、温控和厨电相关的可控设备，例如电视、音箱、灯、空调、风扇、油烟机、烤箱等。"
            "特别注意：门锁、门禁、报警器、烟雾传感器及其他纯感知设备不属于该范围，不能被选中。"
            "特别注意：烟雾传感器和抽油烟机并不相同，烟雾传感器用于检测环境中的烟雾，属于特殊设备，而抽油烟机属于厨电类设备。"
            "特别注意：人体感知设备指的是那些能够感知环境中人体活动的设备，例如运动传感器、人在传感器、人员传感器等，像温度传感器这种感知环境的设备并不属于人体感知设备。"
        )
        select_dev_user = json.dumps(
            {
                "abstract_device": dev,
                "abstract_capability": cap,
                "kind": k,
                "abstract_capability_info": abs_caps,
                "trace_sources": trace_sources,
                "candidates": candidates,
            },
            ensure_ascii=False,
            indent=2,
        )
        _log_stage(
            "device_select.request",
            abstract_capability_count=len(abs_caps),
            abstract_capability_sample=_sample_rows(abs_caps),
            trace_source_count=len(trace_sources),
            trace_source_sample=_sample_rows(trace_sources),
            candidate_count=len(candidates),
            candidate_sample=_sample_rows(
                [
                    {
                        "name": (c.get("name") or "").strip(),
                        "type": (c.get("type") or "").strip(),
                        "capability_count": len(c.get("capabilities") or []),
                    }
                    for c in candidates
                ]
            ),
        )
        try:
            select_raw = simple_completion(
                messages_input=[
                    create_system_message(select_dev_sys),
                    create_user_message(select_dev_user),
                ],
                use_json=True,
                temperature=0.0
            )
            select_parsed = json.loads(select_raw) if isinstance(select_raw, str) else (select_raw or {})
        except Exception:
            select_parsed = {}
        selected_names = (select_parsed or {}).get("selected_devices") or []
        _log_stage(
            "device_select.response",
            raw=select_raw if 'select_raw' in locals() else None,
            parsed=select_parsed,
            selected_names=selected_names if isinstance(selected_names, list) else [],
        )
        if not isinstance(selected_names, list):
            selected_names = []
        selected_set = {str(x).strip() for x in selected_names if str(x).strip()}
        if not selected_set and trace_sources:
            before = sorted(selected_set)
            for s in trace_sources:
                if not isinstance(s, dict):
                    continue
                dn = (s.get("device_name") or "").strip()
                if not dn:
                    continue
                if inst_set and dn not in inst_set:
                    continue
                selected_set.add(dn)
            _log_stage(
                "device_select.trace_fallback",
                before=before,
                after=sorted(selected_set),
                reason="llm_selected_no_devices_use_trace_sources",
            )
        if not selected_set and self._is_targeted_shutdown_device(dev):
            before = sorted(selected_set)
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                name = (c.get("name") or "").strip()
                dtype = (c.get("type") or "").strip()
                if not name:
                    continue
                if inst_set and name not in inst_set:
                    continue
                caps_list = [cap for cap in (c.get("capabilities") or []) if isinstance(cap, dict)]
                if not self._matches_targeted_shutdown_category(name, dtype, "", caps_list):
                    continue
                has_action = any((cap.get("capabilityType") or "").strip().lower() == "action" for cap in caps_list)
                if not has_action:
                    continue
                selected_set.add(name)
            _log_stage(
                "device_select.shutdown_fallback",
                before=before,
                after=sorted(selected_set),
                reason="llm_and_trace_empty_use_targeted_shutdown_fallback",
            )
        if not selected_set:
            _log_stage(
                "abort.no_selected_devices",
                reason="llm_selection_and_fallbacks_empty",
                trace_source_count=len(trace_sources),
                abstract_capability_count=len(abs_caps),
            )
            return []

        dev_candidates = [c for c in candidates if (c.get("name") or "").strip() in selected_set]
        _log_stage(
            "device_select.filtered_candidates",
            selected_device_count=len(selected_set),
            selected_devices=sorted(selected_set),
            dev_candidate_count=len(dev_candidates),
            dev_candidate_sample=_sample_rows(
                [
                    {
                        "name": (c.get("name") or "").strip(),
                        "type": (c.get("type") or "").strip(),
                        "capabilities": [
                            {
                                "name": (cap_info.get("name") or "").strip(),
                                "description": (cap_info.get("description") or "").strip(),
                                "capabilityType": (cap_info.get("capabilityType") or "").strip(),
                            }
                            for cap_info in (c.get("capabilities") or [])[:5]
                            if isinstance(cap_info, dict)
                        ],
                    }
                    for c in dev_candidates
                ]
            ),
        )
        if not dev_candidates:
            _log_stage(
                "abort.no_dev_candidates",
                reason="selected_devices_not_found_in_filtered_candidates",
                selected_devices=sorted(selected_set),
            )
            return []

        select_cap_sys = (
            "你是智能家居能力匹配助手。\n"
            "对于每个具体设备，从其能力列表中选择最能实现给定抽象能力的一个能力名。\n"
            "请结合具体设备名、能力名和能力描述进行语义推理，优先选择语义最一致的能力。\n"
            "只输出 JSON，格式为：{\"mappings\": [{\"device_name\": \"实例名\", \"capability_name\": \"能力名\"}, ...]}。\n"
            "如果某个设备没有合适能力，可以省略该设备。能力名必须严格来自对应设备的候选能力列表，能力参数必须严格对应能力描述。\n"
            "特别注意：不同设备的能力参数可能不同，例如某个人在感知器的返回值可能是'人在家'或'人离开'，而另一个人在感知器的返回值可能是用'true'或'false'来表达人是否在家，需要根据能力描述来选择合适的能力参数。\n"
            "当抽象设备是人体/运动/存在感知类时，应优先选择与人、运动、存在、占用相关的能力，避免将光照、亮度、照度等环境读数能力误选为人的存在读数。"
        )
        select_cap_user = json.dumps(
            {
                "abstract_device": dev,
                "abstract_capability": cap,
                "kind": k,
                "abstract_capability_info": abs_caps,
                "devices": dev_candidates,
            },
            ensure_ascii=False,
            indent=2,
        )
        _log_stage(
            "capability_select.request",
            dev_candidate_count=len(dev_candidates),
            dev_candidate_sample=_sample_rows(
                [
                    {
                        "name": (d.get("name") or "").strip(),
                        "type": (d.get("type") or "").strip(),
                        "capabilities": [
                            {
                                "name": (c.get("name") or "").strip(),
                                "description": (c.get("description") or "").strip(),
                                "capabilityType": (c.get("capabilityType") or "").strip(),
                            }
                            for c in (d.get("capabilities") or [])[:8]
                            if isinstance(c, dict)
                        ],
                    }
                    for d in dev_candidates
                ]
            ),
            abstract_capability_sample=_sample_rows(abs_caps),
        )
        try:
            cap_raw = simple_completion(
                messages_input=[
                    create_system_message(select_cap_sys),
                    create_user_message(select_cap_user),
                ],
                use_json=True,
                temperature=0.0
            )
            cap_parsed = json.loads(cap_raw) if isinstance(cap_raw, str) else (cap_raw or {})
        except Exception:
            cap_parsed = {}
        mappings = (cap_parsed or {}).get("mappings") or []
        _log_stage(
            "capability_select.response",
            raw=cap_raw if 'cap_raw' in locals() else None,
            parsed=cap_parsed,
            mapping_count=len(mappings) if isinstance(mappings, list) else 0,
        )
        if not isinstance(mappings, list):
            mappings = []

        out: list[dict] = []
        for item in mappings:
            if not isinstance(item, dict):
                continue
            dn = (item.get("device_name") or "").strip()
            cn = (item.get("capability_name") or "").strip()
            if not dn or not cn:
                continue
            if inst_set and dn not in inst_set:
                continue
            out.append(
                {
                    "device_name": dn,
                    "capability_name": cn,
                }
            )
        _log_stage(
            "capability_select.filtered",
            output_count=len(out),
            output_sample=_sample_rows(out),
        )
        if not out and trace_sources:
            before_count = len(out)
            for s in trace_sources:
                if not isinstance(s, dict):
                    continue
                dn = (s.get("device_name") or "").strip()
                cn = (s.get("capability_name") or "").strip()
                if not dn or not cn:
                    continue
                if inst_set and dn not in inst_set:
                    continue
                out.append({"device_name": dn, "capability_name": cn})
            _log_stage(
                "capability_select.trace_fallback",
                before_count=before_count,
                after_count=len(out),
                output_sample=_sample_rows(out),
                reason="llm_capability_selection_empty_use_trace_sources",
            )
        if not out and self._is_targeted_shutdown_device(dev):
            def pick_cap(caps_list: list[dict], target: str) -> str:
                names = [((c.get("name") or "").strip()) for c in caps_list if isinstance(c, dict)]
                if not names:
                    return ""
                if "关闭" in target:
                    for key in ["light_off", "turn_off", "off", "close"]:
                        for n in names:
                            if key in n:
                                return n
                if "打开" in target:
                    for key in ["light_on", "turn_on", "on", "open"]:
                        for n in names:
                            if key in n:
                                return n
                if "增加亮度" in target or "升高亮度" in target:
                    for key in ["brightness_up", "increase", "up"]:
                        for n in names:
                            if key in n:
                                return n
                if "减少亮度" in target or "降低亮度" in target:
                    for key in ["brightness_down", "decrease", "down"]:
                        for n in names:
                            if key in n:
                                return n
                return names[0]

            for d in dev_candidates:
                dn = (d.get("name") or "").strip()
                if not dn:
                    continue
                if inst_set and dn not in inst_set:
                    continue
                caps_list = [cap for cap in (d.get("capabilities") or []) if isinstance(cap, dict)]
                picked = pick_cap(caps_list, cap)
                if picked:
                    out.append({"device_name": dn, "capability_name": picked})
            _log_stage(
                "capability_select.shutdown_fallback",
                output_count=len(out),
                output_sample=_sample_rows(out),
                reason="llm_and_trace_empty_use_targeted_shutdown_fallback",
            )

        value_text = (value or "").strip()
        if out and k == "trigger" and value_text:
            desc_index: dict[tuple[str, str], str] = {}
            for d in dev_candidates:
                dn = (d.get("name") or "").strip()
                if not dn:
                    continue
                for c in (d.get("capabilities") or []):
                    if not isinstance(c, dict):
                        continue
                    cn = (c.get("name") or "").strip()
                    if not cn:
                        continue
                    key = (dn, cn)
                    if key not in desc_index:
                        desc_index[key] = (c.get("description") or "").strip()

            value_inputs = []
            for item in out:
                dn = item.get("device_name") or ""
                cn = item.get("capability_name") or ""
                value_inputs.append(
                    {
                        "device_name": dn,
                        "capability_name": cn,
                        "capability_description": desc_index.get((dn, cn), ""),
                    }
                )

            value_sys = (
                "你是智能家居取值对齐助手。\n"
                "根据抽象条件值、比较符号与具体能力描述，为每个设备选择合适的条件值表达，并判断该比较符是否合理。\n"
                "如果比较符与该能力的返回值类型/语义不匹配，就丢弃该设备映射。\n"
                "例如：返回 true/false、开/关、有人/无人 这类布尔或状态能力，通常只能使用 == 或 !=，不应使用 >、<、>=、<=。\n"
                "数值型读数能力才适合使用 >、<、>=、<= 这类阈值比较。\n"
                "特别注意：抽象条件值可能以字符串形式提供，例如 \"true\"、\"false\"、\"开\"、\"关\"、\"有人\"、\"无人\"、\"人离开\"、\"人在家\"。\n"
                "特别注意：当抽象条件值是字符串 \"true\" 或 \"false\" 时，必须按布尔值 true/false 理解，而不是按普通文本或数值字符串理解。\n"
                "特别注意：对于返回布尔值的能力，如果比较符是 == 或 !=，则 \"true\" 和 \"false\" 是合法条件值。\n"
                "示例1：能力描述为“检测是否有人经过，如果有人则返回true，无人则返回false”时，抽象值 \"false\" 且比较符为 ==，应保留并对齐为 false。\n"
                "只输出 JSON："
                "{\"mappings\": ["
                "{\"device_name\": \"...\", \"capability_name\": \"...\", \"keep\": true, \"value\": \"...\", \"reason\": \"...\"},"
                "{\"device_name\": \"...\", \"capability_name\": \"...\", \"keep\": false, \"reason\": \"operator 与能力语义不匹配\"}"
                "]}。\n"
                "要求：对输入中的每个设备都返回一条结果；keep=true 时必须提供 value；keep=false 时不要提供 value。"
            )
            value_user = json.dumps(
                {
                    "abstract_device": dev,
                    "abstract_capability": cap,
                    "abstract_value": value_text,
                    "operator": operator or "",
                    "abstract_capability_info": abs_caps,
                    "devices": value_inputs,
                },
                ensure_ascii=False,
                indent=2,
            )
            _log_stage(
                "value_align.request",
                abstract_value=value_text,
                operator=operator or "",
                device_count=len(value_inputs),
                device_sample=_sample_rows(value_inputs),
            )
            try:
                value_raw = simple_completion(
                    messages_input=[
                        create_system_message(value_sys),
                        create_user_message(value_user),
                    ],
                    use_json=True,
                    temperature=0.0,
                )
                value_parsed = json.loads(value_raw) if isinstance(value_raw, str) else (value_raw or {})
            except Exception:
                value_parsed = {}
            value_mappings = (value_parsed or {}).get("mappings") or []
            _log_stage(
                "value_align.response",
                raw=value_raw if 'value_raw' in locals() else None,
                parsed=value_parsed,
                mapping_count=len(value_mappings) if isinstance(value_mappings, list) else 0,
            )
            if isinstance(value_mappings, list) and value_mappings:
                value_map: dict[tuple[str, str], str] = {}
                kept_keys: set[tuple[str, str]] = set()
                numeric_operators = {"<", "<=", ">", ">="}
                rejected_items: list[dict] = []
                for item in value_mappings:
                    if not isinstance(item, dict):
                        continue
                    dn = (item.get("device_name") or "").strip()
                    cn = (item.get("capability_name") or "").strip()
                    if not dn or not cn:
                        continue
                    if item.get("keep") is not True:
                        rejected_items.append(
                            {
                                "device_name": dn,
                                "capability_name": cn,
                                "reason": item.get("reason") or "keep_is_not_true",
                            }
                        )
                        continue
                    vv = item.get("value")
                    vv_text = value_text if vv is None else str(vv).strip()
                    if (vv_text.startswith('"') and vv_text.endswith('"')) or (vv_text.startswith("'") and vv_text.endswith("'")):
                        vv_text = vv_text[1:-1]
                    if not vv_text:
                        rejected_items.append(
                            {
                                "device_name": dn,
                                "capability_name": cn,
                                "reason": "empty_value_after_alignment",
                            }
                        )
                        continue
                    # 再做一层本地兜底，避免布尔值配上数值比较符。
                    if (operator or "").strip() in numeric_operators and vv_text.lower() in {"true", "false"}:
                        rejected_items.append(
                            {
                                "device_name": dn,
                                "capability_name": cn,
                                "reason": "boolean_value_with_numeric_operator",
                                "value": vv_text,
                            }
                        )
                        continue
                    key = (dn, cn)
                    kept_keys.add(key)
                    value_map[key] = vv_text
                _log_stage(
                    "value_align.filtered",
                    kept_count=len(kept_keys),
                    kept_sample=[
                        {
                            "device_name": dn,
                            "capability_name": cn,
                            "value": value_map.get((dn, cn), ""),
                        }
                        for dn, cn in list(kept_keys)[:5]
                    ],
                    rejected_count=len(rejected_items),
                    rejected_sample=_sample_rows(rejected_items),
                )
                if kept_keys:
                    filtered_out: list[dict] = []
                    for item in out:
                        key = (item.get("device_name") or "", item.get("capability_name") or "")
                        if key not in kept_keys:
                            continue
                        new_item = dict(item)
                        if key in value_map:
                            new_item["value"] = value_map[key]
                        filtered_out.append(new_item)
                    out = filtered_out
                else:
                    _log_stage(
                        "abort.value_align_all_dropped",
                        reason="all_trigger_candidates_removed_in_value_alignment",
                        value_mappings=value_mappings,
                    )
                    return []

        if out:
            _log_stage(
                "success",
                output_count=len(out),
                output_sample=_sample_rows(out, limit=10),
            )
            return out

        fallback: list[dict] = []
        for d in dev_candidates:
            n = (d.get("name") or "").strip()
            caps_list = [c for c in (d.get("capabilities") or []) if isinstance(c, dict)]
            if not caps_list:
                continue
            c0 = caps_list[0]
            cn = (c0.get("name") or "").strip() or cap
            if inst_set and n not in inst_set:
                continue
            fallback.append(
                {
                    "device_name": n,
                    "capability_name": cn,
                }
            )
        _log_stage(
            "fallback.final_first_capability",
            reason="no_capability_mapping_after_all_steps_use_first_capability",
            output_count=len(fallback),
            output_sample=_sample_rows(fallback, limit=10),
        )
        return fallback
