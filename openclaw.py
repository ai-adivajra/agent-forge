from pathlib import Path
import json

from config import SETTINGS, expand


class OpenClaw:
    """
    Read-only interface to the OpenClaw configuration.
    Never modifies the configuration file.
    """

    def __init__(self):

        self.config_file = expand(SETTINGS["openclaw"]["config"])

        if not self.config_file.exists():
            raise FileNotFoundError(
                f"OpenClaw config not found: {self.config_file}\n"
                "Make sure OpenClaw has been initialised at least once."
            )

        with open(self.config_file, "r", encoding="utf-8") as f:
            self.data = json.load(f)

    # ------------------------------------------------------------------
    # MODELS
    # ------------------------------------------------------------------

    @property
    def primary_model(self) -> str:
        return self.data["agents"]["defaults"]["model"]["primary"]

    @property
    def fallback_models(self) -> list[str]:
        return (
            self.data["agents"]["defaults"]["model"]
            .get("fallbacks", [])
        )

    @property
    def embedding_model(self) -> str:
        return (
            self.data["agents"]["defaults"]
            ["memorySearch"]["model"]
        )

    # ------------------------------------------------------------------
    # WORKSPACE
    # ------------------------------------------------------------------

    @property
    def workspace(self) -> Path:
        return Path(self.data["agents"]["defaults"]["workspace"])

    # ------------------------------------------------------------------
    # SESSIONS
    # ------------------------------------------------------------------

    @property
    def sessions_dir(self) -> Path:
        return (
            Path.home()
            / ".openclaw"
            / "agents"
            / "main"
            / "sessions"
        )

    def session_files(self) -> list[Path]:
        """
        Returns only real conversation files.
        Ignores:
            - *.trajectory.jsonl
            - *.reset.*
            - sessions.json
        """

        if not self.sessions_dir.exists():
            raise FileNotFoundError(
                f"Sessions directory not found: {self.sessions_dir}"
            )

        sessions = []

        for f in self.sessions_dir.glob("*.jsonl"):

            name = f.name

            if ".trajectory." in name:
                continue

            if ".reset." in name:
                continue

            if name == "sessions.json":
                continue

            sessions.append(f)

        sessions.sort(
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        return sessions

    def latest_session(self) -> Path:

        sessions = self.session_files()

        if not sessions:
            raise FileNotFoundError(
                f"No OpenClaw sessions found in {self.sessions_dir}"
            )

        return sessions[0]

    def get_session(self, name: str) -> Path:
        """Return a session file by partial name match."""
        for f in self.session_files():
            if name in f.name:
                return f
        raise FileNotFoundError(f"No session matching '{name}'")

    # ------------------------------------------------------------------
    # OBSIDIAN
    # ------------------------------------------------------------------

    @property
    def obsidian_root(self) -> Path:
        return expand(SETTINGS["obsidian"]["vault"])

    @property
    def queue_dir(self) -> Path:
        return self.obsidian_root / SETTINGS["obsidian"]["queue"]

    @property
    def knowledge_dir(self) -> Path:
        return self.obsidian_root / SETTINGS["obsidian"]["knowledge"]

    @property
    def templates_dir(self) -> Path:
        return self.obsidian_root / "Templates"

    # ------------------------------------------------------------------
    # DEBUG
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        return {
            "primary_model":   self.primary_model,
            "fallbacks":       self.fallback_models,
            "embedding_model": self.embedding_model,
            "workspace":       str(self.workspace),
            "sessions_dir":    str(self.sessions_dir),
            "knowledge_dir":   str(self.knowledge_dir),
            "queue_dir":       str(self.queue_dir),
        }
