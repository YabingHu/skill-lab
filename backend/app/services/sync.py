"""中文 -> 英文 同步。

流程：
1. 找出自上次对齐（skill_meta.en_synced_seq）以来中文目录的变更文件；
2. 每个文件让本地 LLM 基于「中文旧版 / 中文新版 / 中文 diff / 当前英文」生成英文新版；
3. 后端把「当前英文 vs LLM 建议」切成若干 hunk，前端逐块勾选（人工确认哪些要改）；
4. 应用时只合入勾选的 hunk（也可以直接提交人工编辑后的全文），生成新版本并记录对齐点。
"""
from __future__ import annotations

import difflib
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

from ..jobs import Job
from .llm import LLMClient, strip_fence
from .skills import SkillStore, _is_text

SYSTEM_PROMPT = """You are a senior technical writer maintaining bilingual (Chinese/English) agent skill documents.
The Chinese version is the source of truth. Your job is to keep the English version in sync.
Rules:
- Output ONLY the complete English file content wrapped in <en_file> and </en_file>. No explanations.
- Preserve markdown structure, front matter keys, code blocks, shell commands, file paths, URLs,
  API names, parameter names and placeholders exactly as they are.
- Translate naturally and concisely; use imperative mood for steps."""

# 文件内容用 XML 标签包裹：skill 文档里本身就有 ``` 代码块，用 ``` 包裹会让边界产生歧义
UPDATE_PROMPT = """The Chinese file `{path}` has been modified. Update the English file accordingly.

IMPORTANT: Only change the parts of the English file that correspond to the Chinese changes shown in the diff.
Every other line of the English file must stay EXACTLY the same (do not rephrase, reformat or re-translate it).

<zh_diff>
{zh_diff}
</zh_diff>

<zh_new>
{zh_new}
</zh_new>

<en_current>
{en_old}
</en_current>

Now output the complete updated English file inside <en_file></en_file>."""

FULL_PROMPT = """Check the English file `{path}` against the Chinese source and fix anything that is missing,
outdated or inconsistent. Lines that are already correct must stay EXACTLY the same.

<zh_new>
{zh_new}
</zh_new>

<en_current>
{en_old}
</en_current>

Now output the complete corrected English file inside <en_file></en_file>."""

TRANSLATE_PROMPT = """Translate the following Chinese file `{path}` into English.

<zh_new>
{zh_new}
</zh_new>

Output the complete English file inside <en_file></en_file>."""


def extract_en_file(text: str) -> str:
    m = re.search(r"<en_file>\n?(.*?)\n?</en_file>", text, re.S)
    if m:
        return m.group(1)
    if "<en_file>" in text:  # 输出被截断，没有闭合标签
        return text.split("<en_file>", 1)[1].lstrip("\n")
    return strip_fence(text)


def compute_hunks(old: str, new: str, context: int = 2) -> list[dict]:
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    hunks = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for idx, (tag, i1, i2, j1, j2) in enumerate(sm.get_opcodes()):
        if tag == "equal":
            continue
        hunks.append({
            "id": idx,
            "tag": tag,
            "old_start": i1 + 1,
            "new_start": j1 + 1,
            "old_lines": [line.rstrip("\n") for line in a[i1:i2]],
            "new_lines": [line.rstrip("\n") for line in b[j1:j2]],
            "context_before": [line.rstrip("\n") for line in a[max(0, i1 - context):i1]],
            "context_after": [line.rstrip("\n") for line in a[i2:i2 + context]],
        })
    return hunks


def apply_hunks(old: str, new: str, selected: set[int]) -> str:
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    out: list[str] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for idx, (tag, i1, i2, j1, j2) in enumerate(sm.get_opcodes()):
        if tag == "equal" or idx not in selected:
            out.extend(a[i1:i2])
        else:
            chunk = b[j1:j2]
            # 合入的块落在文件中间而自身没有换行结尾时补一个换行
            if chunk and not chunk[-1].endswith("\n") and i2 < len(a):
                chunk = chunk[:-1] + [chunk[-1] + "\n"]
            out.extend(chunk)
    return "".join(out)


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class SyncService:
    def __init__(self, store: SkillStore, llm: LLMClient):
        self.store = store
        self.llm = llm

    def pending(self, skill: str) -> dict:
        """中文自上次对齐以来的变更文件（不调用 LLM）。"""
        base_seq = self.store.en_synced_seq(skill)
        base_zh = self.store.version_files(skill, base_seq)["zh"] if base_seq else {}
        cur = self.store.working_files(skill)
        zh, en = cur["zh"], cur["en"]
        items = []
        for path in sorted(set(base_zh) | set(zh)):
            old, new = base_zh.get(path), zh.get(path)
            if base_seq is None:
                if new is not None and path not in en:
                    items.append({"path": path, "zh_status": "added"})
                continue
            if old == new:
                continue
            if new is None:
                if path in en:
                    items.append({"path": path, "zh_status": "deleted"})
            else:
                items.append({"path": path, "zh_status": "added" if old is None else "modified"})
        # 中文存在但英文缺失的文件，也算待同步
        for path in zh:
            if path not in en and not any(i["path"] == path for i in items):
                items.append({"path": path, "zh_status": "missing_en"})
        return {"base_seq": base_seq, "items": items}

    def propose(self, job: Job, skill: str, paths: list[str] | None = None, mode: str = "diff") -> dict:
        """mode=diff：只处理待同步文件；mode=full：对指定文件（默认全部）做全量一致性检查。"""
        base_seq = self.store.en_synced_seq(skill)
        base_zh = self.store.version_files(skill, base_seq)["zh"] if base_seq else {}
        cur = self.store.working_files(skill)
        zh, en = cur["zh"], cur["en"]

        if mode == "full":
            targets = [{"path": p, "zh_status": "check"} for p in (paths or sorted(zh))]
        else:
            targets = self.pending(skill)["items"]
            if paths:
                targets = [t for t in targets if t["path"] in paths]
        job.log(f"基线版本：{'v' + str(base_seq) if base_seq else '无（首次同步）'}，待处理 {len(targets)} 个文件")

        def work(t: dict) -> dict:
            path = t["path"]
            zh_new_b, zh_old_b, en_old_b = zh.get(path), base_zh.get(path), en.get(path)
            en_old = en_old_b.decode("utf-8") if en_old_b is not None and _is_text(en_old_b) else ""
            item = {"path": path, "zh_status": t["zh_status"], "en_exists": en_old_b is not None,
                    "en_old": en_old, "en_old_digest": _digest(en_old)}
            if zh_new_b is None:
                item.update(action="delete", en_new="", hunks=[], note="中文文件已删除，建议删除英文文件")
                return item
            if not _is_text(zh_new_b):
                item.update(action="copy", en_new="", hunks=[], note="二进制文件，建议原样复制")
                return item
            zh_new = zh_new_b.decode("utf-8")
            zh_old = zh_old_b.decode("utf-8", "replace") if zh_old_b is not None else ""
            if en_old_b is None:
                prompt, action = TRANSLATE_PROMPT.format(path=path, zh_new=zh_new), "create"
            elif mode == "full" or zh_old_b is None:
                prompt, action = FULL_PROMPT.format(path=path, zh_new=zh_new, en_old=en_old), "modify"
            else:
                zh_diff = "".join(difflib.unified_diff(
                    zh_old.splitlines(keepends=True), zh_new.splitlines(keepends=True), "zh(old)", "zh(new)", n=3))
                prompt = UPDATE_PROMPT.format(path=path, zh_diff=zh_diff, zh_new=zh_new, en_old=en_old)
                action = "modify"
                item["zh_diff"] = zh_diff
            job.log(f"→ LLM 处理 {path}（{action}）")
            try:
                raw = self.llm.chat([
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ])
                en_new = extract_en_file(raw)
                if "<en_file>" in raw and "</en_file>" not in raw:
                    item["note"] = "⚠ LLM 输出可能被截断（没有 </en_file> 结束标记），请仔细核对，必要时调大 llm.max_tokens"
            except Exception as e:  # noqa: BLE001
                job.log(f"  ✗ {path} 失败：{e}")
                item.update(action="error", en_new="", hunks=[], note=str(e))
                return item
            if en_old.endswith("\n") and not en_new.endswith("\n"):
                en_new += "\n"
            if action == "create" and zh_new.endswith("\n") and not en_new.endswith("\n"):
                en_new += "\n"
            hunks = compute_hunks(en_old, en_new)
            item.update(action=action if hunks else "unchanged", en_new=en_new, hunks=hunks,
                        zh_new=zh_new, zh_old=zh_old)
            job.log(f"  ✓ {path}：{len(hunks)} 处修改建议")
            return item

        workers = max(1, int(self.llm.conf.get("concurrency", 2)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            items = list(pool.map(work, targets))
        return {"skill": skill, "base_seq": base_seq, "mode": mode, "items": items}

    def apply(self, skill: str, items: list[dict], message: str = "", mark_synced: bool = True) -> dict:
        """items: [{path, action, en_old, en_new, selected:[hunk id], content?(人工编辑后的全文)}]"""
        cur_en = self.store.working_files(skill)["en"]
        cur_zh = self.store.working_files(skill)["zh"]
        changes: dict[str, str | None] = {}
        conflicts = []
        for it in items:
            path = it["path"]
            cur = cur_en.get(path)
            cur_text = cur.decode("utf-8", "replace") if cur is not None else ""
            if it.get("en_old_digest") and _digest(cur_text) != it["en_old_digest"]:
                conflicts.append(path)
                continue
            action = it.get("action")
            if action == "delete":
                if it.get("selected") or it.get("accept"):
                    changes[path] = None
            elif action == "copy":
                if it.get("accept") and path in cur_zh:
                    # 二进制直接复制
                    target = self.store._safe_join(self.store.lang_dir(skill, "en"), path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(cur_zh[path])
            elif it.get("content") is not None:
                changes[path] = it["content"]
            elif it.get("selected"):
                changes[path] = apply_hunks(it.get("en_old", ""), it.get("en_new", ""), set(it["selected"]))
        if conflicts:
            return {"ok": False, "conflicts": conflicts,
                    "message": "这些英文文件在生成建议后被修改过，请重新生成：" + "、".join(conflicts)}
        n = self.store.apply_changes(skill, {"en": changes}) if changes else 0
        version = self.store.create_version(skill, message or f"LLM 同步英文（{n} 个文件）", source="llm-sync")
        latest = self.store.latest_version(skill)
        if mark_synced and latest:
            self.store.set_en_synced(skill, latest["seq"])
        return {"ok": True, "changed": n, "version": version["seq"] if version else (latest or {}).get("seq")}
