#!/usr/bin/env python3

import json
import re
import requests

from config import SETTINGS


class OllamaError(Exception):
    pass


class Ollama:

    def __init__(self, host: str | None = None, timeout: int | None = None):

        cfg = SETTINGS.get("ollama", {})

        self.host    = (host    or cfg.get("host",    "http://127.0.0.1:11434")).rstrip("/")
        self.timeout = (timeout or cfg.get("timeout", 600))

    # ------------------------------------------------------------------
    # Low-level chat
    # ------------------------------------------------------------------

    def build_request(
        self,
        model:       str,
        system:      str,
        user:        str,
        temperature: float = 0.2,
        stream:      bool  = False,
        force_json:  bool  = False,
    ) -> dict:
        """Return the exact JSON dict that will be sent to /api/chat."""
        payload: dict = {
            "model":  model,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "options": {
                "temperature": temperature,
            },
        }
        if force_json:
            # Forces Ollama to constrain output to valid JSON.
            # Critical for long sessions where the model may drift from
            # the system prompt and respond to the conversation instead.
            payload["format"] = "json"
        return payload

    def chat(
        self,
        model:       str,
        system:      str,
        user:        str,
        temperature: float = 0.2,
        stream:      bool  = False,
        force_json:  bool  = False,
    ) -> str:

        payload = self.build_request(
            model=model,
            system=system,
            user=user,
            temperature=temperature,
            stream=stream,
            force_json=force_json,
        )

        try:
            r = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}. "
                "Make sure the service is running: systemctl --user start ollama"
            )
        except requests.exceptions.Timeout:
            raise OllamaError(
                f"Ollama timed out after {self.timeout}s. "
                "Try a smaller model or increase the timeout in settings.yaml."
            )
        except requests.exceptions.HTTPError as e:
            raise OllamaError(f"Ollama HTTP error: {e} — {r.text[:300]}")

        return r.json()["message"]["content"]

    # ------------------------------------------------------------------
    # JSON extraction (strips markdown fences if present)
    # ------------------------------------------------------------------

    def json(
        self,
        model:  str,
        system: str,
        user:   str,
    ) -> dict | list:

        answer = self.chat(model=model, system=system, user=user, force_json=True)
        raw    = answer  # preserve for error reporting

        # 1. Strip ```json … ``` or ``` … ``` fences
        answer = answer.strip()
        answer = re.sub(r"^```(?:json)?\s*", "", answer)
        answer = re.sub(r"\s*```$",          "", answer)
        answer = answer.strip()

        # 2. Try direct parse
        try:
            return json.loads(answer)
        except json.JSONDecodeError:
            pass

        # 3. Extract first { … } or [ … ] block (model wrapped in prose/markdown)
        for pattern in (r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"):
            m = re.search(pattern, answer)
            if m:
                candidate = m.group(1)
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

        # 4. Nothing worked
        raise OllamaError(
            f"Model returned invalid JSON.\n"
            f"Raw response (first 500 chars):\n{raw[:500]}"
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Return True if Ollama is reachable."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return names of locally available models."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=10)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception as e:
            raise OllamaError(f"Could not list Ollama models: {e}")

    def embed(self, model: str, text: str) -> list[float]:
        """
        Generate an embedding vector for the given text.
        Uses Ollama /api/embed endpoint.
        The model should be an embedding model (e.g. nomic-embed-text, mxbai-embed-large).
        """
        payload = {"model": model, "input": text}

        try:
            r = requests.post(
                f"{self.host}/api/embed",
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}. "
                "Make sure the service is running: systemctl --user start ollama"
            )
        except requests.exceptions.HTTPError as e:
            raise OllamaError(f"Ollama embed HTTP error: {e} — {r.text[:300]}")

        data = r.json()

        # Ollama returns { "embeddings": [[...]] } (list of lists)
        embeddings = data.get("embeddings")
        if not embeddings or not embeddings[0]:
            raise OllamaError(f"Ollama returned empty embedding for model '{model}'")

        return embeddings[0]
