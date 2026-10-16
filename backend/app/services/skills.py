"""Skill 工作区与多版本管理。

- 工作区：config.skills.root 下按 layout 组织的中英文目录，网页编辑直接写这里。
- 版本：每次保存会把该 skill 的中英文目录完整快照到 data/versions/<skill>/<seq>/<lang>/，
  元数据记录在 versions 表。回退 = 把某个快照复制回工作区并生成一个新版本，所以历史不会丢。
"""
from __future__ import annotations

import difflib
import hashlib
import shutil
from pathlib import Path

from ..config import Config
from ..db import DB, dumps, loads, now

TEXT_SUFFIX_MAX = 2 * 1024 * 1024  # 超过 2MB 的文件不在网页里编辑


class SkillError(Exception):
    pass


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class SkillStore:
    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self.versions_root = cfg.data_dir / "versions"
        self.versions_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 路径
    @property
    def langs(self) -> dict[str, str]:
        return self.cfg["skills"]["langs"]

    @property
    def layout(self) -> str:
        return self.cfg["skills"]["layout"]

    def lang_dir(self, skill: str, lang: str, root: Path | None = None) -> Path:
        self._check_name(skill)
        if lang not in self.langs:
            raise SkillError(f"未知语言 {lang}")
        rel = self.layout.format(lang=self.langs[lang], skill=skill)
        return (root or self.cfg.skills_root) / rel

    def snapshot_dir(self, skill: str, seq: int) -> Path:
        return self.versions_root / skill / str(seq)

    @staticmethod
    def _check_name(name: str) -> None:
        if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
            raise SkillError(f"非法的 skill 名称：{name!r}")

    @staticmethod
    def _safe_join(base: Path, rel: str) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        p = (base / rel).resolve()
        if base.resolve() != p and base.resolve() not in p.parents:
            raise SkillError(f"非法路径：{rel}")
        return p

    # ------------------------------------------------------------------ 列表
    def skill_names(self, root: Path | None = None) -> list[str]:
        """按 layout 扫描 root（默认工作区）下的 skill 名称。"""
        root = root or self.cfg.skills_root
        names: set[str] = set()
        parts = self.layout.split("/")
        if parts[0] == "{lang}":
            for lang_dirname in self.langs.values():
                d = root / lang_dirname
                if d.is_dir():
                    names.update(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
        else:
            if root.is_dir():
                for p in root.iterdir():
                    if p.is_dir() and not p.name.startswith(".") and any(
                        (p / ld).is_dir() for ld in self.langs.values()
                    ):
                        names.add(p.name)
        return sorted(names)

    def list_skills(self) -> list[dict]:
        names = self.skill_names()
        out = []
        for name in sorted(names):
            latest = self.latest_version(name)
            meta = self.db.one("SELECT * FROM skill_meta WHERE skill=?", (name,)) or {}
            out.append({
                "name": name,
                "langs": {lang: self.lang_dir(name, lang).is_dir() for lang in self.langs},
                "latest_seq": latest["seq"] if latest else None,
                "latest_at": latest["created_at"] if latest else None,
                "en_synced_seq": meta.get("en_synced_seq"),
            })
        return out

    def create_skill(self, name: str, description: str = "") -> None:
        self._check_name(name)
        main = self.cfg["skills"]["main_file"]
        for lang in self.langs:
            d = self.lang_dir(name, lang)
            if d.exists():
                raise SkillError(f"skill {name} 已存在")
        zh_tpl = f"---\nname: {name}\ndescription: {description or '在这里描述 skill 的用途'}\n---\n\n# {name}\n\n## 步骤\n\n1. 第一步\n2. 第二步\n"
        en_tpl = f"---\nname: {name}\ndescription: {description or 'Describe what this skill does'}\n---\n\n# {name}\n\n## Steps\n\n1. Step one\n2. Step two\n"
        for lang in self.langs:
            d = self.lang_dir(name, lang)
            d.mkdir(parents=True)
            (d / main).write_text(zh_tpl if lang == "zh" else en_tpl, encoding="utf-8")
        self.create_version(name, "新建 skill", source="import")

    # ------------------------------------------------------------------ 文件
    def scan_dir(self, base: Path) -> dict[str, bytes]:
        return self._scan(base)

    def replace_lang_dir(self, skill: str, lang: str, files: dict[str, bytes]) -> None:
        """用给定文件完整替换工作区中某个语言目录（多出的文件会删除）。"""
        base = self.lang_dir(skill, lang)
        for rel in self._scan(base):
            if rel not in files:
                self._safe_join(base, rel).unlink()
        for rel, data in files.items():
            p = self._safe_join(base, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        base.mkdir(parents=True, exist_ok=True)

    def _scan(self, base: Path) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        if not base.is_dir():
            return files
        ignore = set(self.cfg["skills"]["ignore"])
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(base).as_posix()
            if any(part in ignore for part in rel.split("/")):
                continue
            files[rel] = p.read_bytes()
        return files

    def working_files(self, skill: str) -> dict[str, dict[str, bytes]]:
        return {lang: self._scan(self.lang_dir(skill, lang)) for lang in self.langs}

    def version_files(self, skill: str, seq: int) -> dict[str, dict[str, bytes]]:
        snap = self.snapshot_dir(skill, seq)
        if not snap.is_dir():
            raise SkillError(f"版本 v{seq} 不存在")
        return {lang: self._scan(snap / lang) for lang in self.langs}

    def files_at(self, skill: str, ref: str | int | None) -> dict[str, dict[str, bytes]]:
        """ref: None / 'working' 表示工作区，数字表示版本号。"""
        if ref in (None, "", "working"):
            return self.working_files(skill)
        return self.version_files(skill, int(ref))

    def tree(self, skill: str) -> dict:
        files = self.working_files(skill)
        out = {}
        for lang, fs in files.items():
            out[lang] = [
                {"path": p, "size": len(b), "text": _is_text(b) and len(b) <= TEXT_SUFFIX_MAX}
                for p, b in fs.items()
            ]
        return out

    def read_file(self, skill: str, lang: str, path: str, ref: str | int | None = None) -> str:
        if ref in (None, "", "working"):
            p = self._safe_join(self.lang_dir(skill, lang), path)
            if not p.is_file():
                raise SkillError(f"文件不存在：{lang}/{path}")
            data = p.read_bytes()
        else:
            snap = self.snapshot_dir(skill, int(ref)) / lang
            p = self._safe_join(snap, path)
            if not p.is_file():
                raise SkillError(f"v{ref} 中不存在：{lang}/{path}")
            data = p.read_bytes()
        if not _is_text(data):
            raise SkillError("二进制文件不支持在线编辑")
        return data.decode("utf-8")

    def apply_changes(self, skill: str, changes: dict[str, dict[str, str | None]]) -> int:
        """changes: {lang: {path: content 或 None(删除)}}，返回变更文件数。"""
        n = 0
        for lang, files in changes.items():
            base = self.lang_dir(skill, lang)
            for rel, content in files.items():
                p = self._safe_join(base, rel)
                if content is None:
                    if p.exists():
                        p.unlink()
                        n += 1
                        # 清理空目录
                        parent = p.parent
                        while parent != base and parent.exists() and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                old = p.read_bytes() if p.exists() else None
                new = content.encode("utf-8")
                if old != new:
                    p.write_bytes(new)
                    n += 1
        return n

    # ------------------------------------------------------------------ 版本
    def latest_version(self, skill: str) -> dict | None:
        return self.db.one("SELECT * FROM versions WHERE skill=? ORDER BY seq DESC LIMIT 1", (skill,))

    def get_version(self, skill: str, seq: int) -> dict | None:
        row = self.db.one("SELECT * FROM versions WHERE skill=? AND seq=?", (skill, seq))
        if row:
            row["files"] = loads(row.pop("files_json"), {})
        return row

    def list_versions(self, skill: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM versions WHERE skill=? ORDER BY seq DESC", (skill,))
        deps = self.db.query("SELECT version_seq, COUNT(*) AS n, MAX(created_at) AS last FROM deployments "
                             "WHERE skill=? AND status!='failed' GROUP BY version_seq", (skill,))
        dep_map = {d["version_seq"]: d for d in deps}
        prev_files = None
        # 计算每个版本相对上一个版本的变更摘要（从旧到新遍历）
        for row in reversed(rows):
            files = loads(row.pop("files_json"), {})
            row["changes"] = _summarize(prev_files, files)
            row["file_count"] = sum(len(v) for v in files.values())
            prev_files = files
            d = dep_map.get(row["seq"])
            row["deploy_count"] = d["n"] if d else 0
            row["last_deploy_at"] = d["last"] if d else None
        return rows

    def _hashes(self, files: dict[str, dict[str, bytes]]) -> dict[str, dict[str, str]]:
        return {lang: {p: _sha1(b) for p, b in fs.items()} for lang, fs in files.items()}

    def is_dirty(self, skill: str) -> bool:
        latest = self.latest_version(skill)
        if not latest:
            return True
        return self._hashes(self.working_files(skill)) != loads(latest["files_json"], {})

    def ensure_initial_version(self, skill: str) -> None:
        if not self.latest_version(skill):
            self.create_version(skill, "初始导入", source="import")

    def create_version(self, skill: str, message: str, source: str = "manual",
                       author: str = "", force: bool = False) -> dict | None:
        files = self.working_files(skill)
        hashes = self._hashes(files)
        latest = self.latest_version(skill)
        if latest and not force and loads(latest["files_json"], {}) == hashes:
            return None  # 没有变化，不产生新版本
        seq = (latest["seq"] + 1) if latest else 1
        snap = self.snapshot_dir(skill, seq)
        if snap.exists():
            shutil.rmtree(snap)
        for lang, fs in files.items():
            for rel, data in fs.items():
                p = snap / lang / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            (snap / lang).mkdir(parents=True, exist_ok=True)
        self.db.execute(
            "INSERT INTO versions(skill, seq, message, source, author, parent_seq, files_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (skill, seq, message, source, author, latest["seq"] if latest else None, dumps(hashes), now()),
        )
        if source in ("import", "env-pull") and latest is None:
            # 首次导入视为中英文已对齐
            self.set_en_synced(skill, seq)
        return self.get_version(skill, seq)

    def restore(self, skill: str, seq: int, langs: list[str] | None = None, message: str = "") -> dict | None:
        snap_files = self.version_files(skill, seq)
        langs = langs or list(self.langs)
        for lang in langs:
            base = self.lang_dir(skill, lang)
            current = self._scan(base)
            target = snap_files.get(lang, {})
            for rel in current:
                if rel not in target:
                    (self._safe_join(base, rel)).unlink()
            for rel, data in target.items():
                p = self._safe_join(base, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        label = "、".join(langs)
        return self.create_version(skill, message or f"回退到 v{seq}（{label}）", source="rollback")

    def set_en_synced(self, skill: str, seq: int | None) -> None:
        self.db.execute(
            "INSERT INTO skill_meta(skill, en_synced_seq) VALUES (?, ?) "
            "ON CONFLICT(skill) DO UPDATE SET en_synced_seq=excluded.en_synced_seq",
            (skill, seq),
        )

    def en_synced_seq(self, skill: str) -> int | None:
        row = self.db.one("SELECT en_synced_seq FROM skill_meta WHERE skill=?", (skill,))
        return row["en_synced_seq"] if row else None

    # ------------------------------------------------------------------ diff
    def diff(self, skill: str, a: str | int | None, b: str | int | None) -> list[dict]:
        """比较两个版本（或工作区），返回每个变更文件的新旧内容，前端用 MergeView 渲染。"""
        fa, fb = self.files_at(skill, a), self.files_at(skill, b)
        out = []
        for lang in self.langs:
            la, lb = fa.get(lang, {}), fb.get(lang, {})
            for path in sorted(set(la) | set(lb)):
                da, db_ = la.get(path), lb.get(path)
                if da == db_:
                    continue
                status = "added" if da is None else "deleted" if db_ is None else "modified"
                text_a = da.decode("utf-8", "replace") if da is not None and _is_text(da) else ("" if da is None else "<二进制文件>")
                text_b = db_.decode("utf-8", "replace") if db_ is not None and _is_text(db_) else ("" if db_ is None else "<二进制文件>")
                add, rem = _line_stats(text_a, text_b)
                out.append({"lang": lang, "path": path, "status": status, "old": text_a, "new": text_b,
                            "additions": add, "deletions": rem})
        return out


def _line_stats(a: str, b: str) -> tuple[int, int]:
    add = rem = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            rem += i2 - i1
        if tag in ("replace", "insert"):
            add += j2 - j1
    return add, rem


def _summarize(prev: dict | None, cur: dict) -> dict:
    out: dict[str, dict[str, int]] = {}
    for lang in set(cur) | set(prev or {}):
        a = (prev or {}).get(lang, {})
        b = cur.get(lang, {})
        out[lang] = {
            "added": sum(1 for p in b if p not in a),
            "deleted": sum(1 for p in a if p not in b),
            "modified": sum(1 for p in b if p in a and a[p] != b[p]),
        }
    return out
