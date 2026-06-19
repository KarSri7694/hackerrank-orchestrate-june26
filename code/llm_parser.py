from openai import OpenAI
from pathlib import Path
from typing import List
import base64


class LLMParser:
    def __init__(self,base_url: str, model: str, api_key: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.messages: list[dict] = []
        self.last_response: str | None = None

    def get_system_prompt(self, file_path: Path) -> str:
        with open(file_path, 'r') as f:
            return f.read()

    def reset_claim_context(self) -> None:
        self.messages = []
        self.last_response = None

    def _encode_images(self, image_path: List[Path]) -> list[str]:
        encoded_images = []
        for image in image_path:
            with open(image, "rb") as f:
                base64_image = base64.b64encode(f.read()).decode("utf-8")
            encoded_images.append(base64_image)
        return encoded_images

    def _build_initial_user_content(self, user_prompt: str, image_path: List[Path]) -> list[dict]:
        encoded_images = self._encode_images(image_path)
        user_content = []
        for base64_image in encoded_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{base64_image}"}
            })

        # Append the original text prompt after the images.
        user_content.append({
            "type": "text",
            "text": user_prompt
        })
        return user_content

    def _ensure_claim_context(
        self,
        system_prompt: str,
        user_prompt: str,
        image_path: List[Path],
    ) -> None:
        if self.messages:
            return

        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._build_initial_user_content(user_prompt, image_path)},
        ]

    def run_interaction(
        self,
        system_prompt: str,
        user_prompt: str,
        image_path: List[Path],
        retry_instruction: str | None = None,
    ):
        self._ensure_claim_context(system_prompt, user_prompt, image_path)

        if retry_instruction:
            if self.last_response:
                self.messages.append({
                    "role": "assistant",
                    "content": self.last_response,
                })
            self.messages.append({
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": retry_instruction
                }],
            })

        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            stream=True,
            extra_body={"reasoning": {"enabled": True}}
        )
        full_response = ""
        for chunk in response:
            content = chunk.choices[0].delta.content or ""
            full_response += content

        self.last_response = full_response
        return full_response
