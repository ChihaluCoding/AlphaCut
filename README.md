# AlphaCut

PySide6 と ToonOut を使うローカル処理版です。画像は外部APIへ送信せず、モデル取得と推論をローカル環境で行います。

## セットアップ

```powershell
cd desktop_app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

CUDA 版 PyTorch を使う場合は、先に公式の環境に合った PyTorch を入れてから `pip install -r requirements.txt` を実行してください。

既存の Python 環境に `torch 2.7.1+cu118` が入っている場合は、`torchvision` も同じ CUDA 11.8 向けに合わせてください。

```powershell
python -m pip install torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu118
python -m pip install timm kornia einops
```

PySide6 の DLL エラーが出る場合は、既存の PySide6 が壊れているかバージョンが合っていない可能性があります。

```powershell
python -m pip install --force-reinstall -r requirements.txt
```

## 注意

- 初回実行時は `joelseytre/toonout` のモデル重みと `ZhengPeng7/BiRefNet` のモデル構造を Hugging Face から取得します。
- GPU がある場合は CUDA を使います。ない場合は CPU で動きますが時間がかかります。
- 透過結果は PNG として保存してください。
