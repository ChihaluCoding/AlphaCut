from __future__ import annotations

import gc
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import torch
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import LocalEntryNotFoundError
from PIL import Image
from torchvision import transforms
from transformers import AutoConfig, AutoModelForImageSegmentation


MODEL_ID = "joelseytre/toonout"
ARCHITECTURE_MODEL_ID = "ZhengPeng7/BiRefNet"
MODEL_WEIGHT_FILE = "birefnet_finetuned_toonout.pth"
IMAGE_SIZE = (1024, 1024)
ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class RemovalResult:
    image: Image.Image
    device_label: str


class BiRefNetRemover:
    def __init__(self) -> None:
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._model = None
        self._transform = transforms.Compose(
            [
                transforms.Resize(IMAGE_SIZE),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )

    @property
    def device_label(self) -> str:
        return "CUDA" if self._device.type == "cuda" else "CPU"

    def remove_background(
        self,
        image_path: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> RemovalResult:
        self._report(progress_callback, 5, "モデルを確認中")
        self._ensure_model(progress_callback)
        self._report(progress_callback, 40, "画像を読み込み中")
        source = Image.open(image_path).convert("RGB")
        self._report(progress_callback, 50, "画像を前処理中")
        tensor = self._move_tensor_to_device(self._transform(source).unsqueeze(0), progress_callback)

        self._report(progress_callback, 60, "背景を解析中")
        predictions = self._predict_with_fallback(tensor, progress_callback)

        self._report(progress_callback, 85, "透過マスクを作成中")
        mask = transforms.ToPILImage()(predictions[0].squeeze()).resize(source.size)
        self._report(progress_callback, 92, "透過画像を合成中")
        output = source.convert("RGBA")
        output.putalpha(mask)
        self._report(progress_callback, 100, "完了")
        return RemovalResult(image=output, device_label=self.device_label)

    def _ensure_model(self, progress_callback: ProgressCallback | None = None) -> None:
        if self._model is not None:
            self._report(progress_callback, 35, "モデル準備済み")
            return

        self._report(progress_callback, 10, "モデル構造を読み込み中")
        config = self._load_config()
        model = AutoModelForImageSegmentation.from_config(config, trust_remote_code=True)
        self._report(progress_callback, 20, "ToonOut重みを取得中")
        weights_path = self._download_weight_file()
        state_dict = self._load_state_dict(weights_path)
        self._report(progress_callback, 28, "ToonOut重みを適用中")
        model.load_state_dict(state_dict, strict=True)
        self._report(progress_callback, 30, "モデルをデバイスへ配置中")
        try:
            model.to(self._device)
        except RuntimeError:
            if self._device.type != "cuda":
                raise
            self._report(progress_callback, 30, "CUDA初期化に失敗したためCPUへ切替中")
            self._device = torch.device("cpu")
            model.to(self._device)
        model.eval()
        self._model = model
        self._report(progress_callback, 35, "モデル準備完了")

    def _load_config(self):
        try:
            return AutoConfig.from_pretrained(
                ARCHITECTURE_MODEL_ID,
                trust_remote_code=True,
                local_files_only=True,
            )
        except (LocalEntryNotFoundError, OSError):
            return AutoConfig.from_pretrained(
                ARCHITECTURE_MODEL_ID,
                trust_remote_code=True,
            )

    def _download_weight_file(self) -> str:
        try:
            return hf_hub_download(
                MODEL_ID,
                filename=MODEL_WEIGHT_FILE,
                local_files_only=True,
            )
        except LocalEntryNotFoundError:
            return hf_hub_download(MODEL_ID, filename=MODEL_WEIGHT_FILE)

    def _predict_with_fallback(
        self,
        tensor: torch.Tensor,
        progress_callback: ProgressCallback | None,
    ) -> torch.Tensor:
        try:
            return self._predict(tensor)
        except RuntimeError:
            if self._device.type != "cuda":
                raise

            self._report(progress_callback, 62, "CUDA推論に失敗したためCPUで再試行中")
            self.release()
            self._device = torch.device("cpu")
            self._ensure_model(progress_callback)
            return self._predict(tensor.cpu())

    def _move_tensor_to_device(
        self,
        tensor: torch.Tensor,
        progress_callback: ProgressCallback | None,
    ) -> torch.Tensor:
        try:
            return tensor.to(self._device)
        except RuntimeError:
            if self._device.type != "cuda":
                raise

            self._report(progress_callback, 52, "CUDA転送に失敗したためCPUへ切替中")
            self.release()
            self._device = torch.device("cpu")
            self._ensure_model(progress_callback)
            return tensor.to(self._device)

    def _predict(self, tensor: torch.Tensor) -> torch.Tensor:
        if self._model is None:
            raise RuntimeError("モデルが準備されていません。")

        with torch.inference_mode():
            return self._model(tensor)[-1].sigmoid().cpu()

    def _load_state_dict(self, weights_path: str) -> dict[str, torch.Tensor]:
        checkpoint = torch.load(weights_path, map_location="cpu")
        if isinstance(checkpoint, dict):
            for key in ("state_dict", "model"):
                value = checkpoint.get(key)
                if isinstance(value, dict):
                    checkpoint = value
                    break

        if not isinstance(checkpoint, dict):
            raise RuntimeError("ToonOut の重みファイルを読み込めませんでした。")

        state_dict: dict[str, torch.Tensor] = {}
        for key, value in checkpoint.items():
            if isinstance(value, torch.Tensor):
                clean_key = key.removeprefix("module.").removeprefix("_orig_mod.")
                state_dict[clean_key] = value
        return state_dict

    def release(self) -> None:
        self._model = None
        gc.collect()
        if self._device.type == "cuda":
            torch.cuda.empty_cache()

    def _report(
        self,
        progress_callback: ProgressCallback | None,
        percent: int,
        message: str,
    ) -> None:
        if progress_callback is not None:
            progress_callback(percent, message)
