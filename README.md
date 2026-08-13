# DeYaz

<p align="center">
  <img src="assets/deyaz-logo.png" width="144" alt="DeYaz logo">
</p>

DeYaz danışığı, audio/video fayllarını və görüşləri mətnə çevirən açıq mənbəli
PyQt6 desktop tətbiqidir. Azərbaycan, türk, ingilis və rus dillərində UI verir.

## Nə edir

- **Səsyazma:** qlobal qısa yol və daşına bilən mini düymə ilə diktə edir,
  nəticəni aktiv tətbiqə əlavə edir.
- **Fayl transkripti:** audio/video oynatma, waveform, timestamp, SRT/TXT/PDF
  export, təmiz transcript və xülasə hazırlayır.
- **Görüş qeydi:** canlı transcript və görüş sonu transcript/xülasə/key point
  nəticələri yaradır.
- **Kontekst:** seçilmiş layihə, fayl və ya yapışdırılmış mətni work mode-a əlavə
  edir; mövcud stack barədə olmayan detalı uydurmur.
- **Provider seçimi:** OpenAI və OpenRouter hesab/açar axınlarını ayrı saxlayır.

## Dəstəklənən platformalar

| Platforma | Paket | Qeyd |
|---|---|---|
| Windows 10/11 | `DeYaz-Windows-x64.zip` | Tam dəstək; Meeting Notes mikrofon + sistem səsi |
| macOS 14+ | `DeYaz-macOS-*.zip` | Mikrofon icazəsi və shortcut/paste üçün Accessibility icazəsi lazımdır |
| Linux x64 | `DeYaz-Linux-x64.tar.gz` | X11-də qlobal shortcut; Wayland bunu məhdudlaşdıra bilər |

macOS və Linux-da Meeting Notes hazırda mic-only işləyir. Sistem səsinin tutulması
OS səviyyəsində virtual audio device və əlavə routing tələb etdiyi üçün yanlış
“tam dəstək” iddiası edilmir. macOS paketi hələ Apple Developer sertifikatı ilə
imzalanmadığından ilk açılışda **Open** təsdiqi lazım ola bilər.

## Quraşdırma

Hazır paketləri [Releases](https://github.com/hasan0v/deyaz/releases) səhifəsindən
endir. Mənbədən işlətmək üçün:

```bash
git clone https://github.com/hasan0v/deyaz.git
cd deyaz
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python deyaz_app.py
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python deyaz_app.py
```

## Build və test

```bash
python -m unittest discover -v
python -m PyInstaller --noconfirm --clean DeYaz.spec
```

PyInstaller cross-compiler deyil. `.github/workflows/build-desktop.yml` hər paketi
öz native GitHub runner-ində qurur və `v*` tag-larında GitHub Release yaradır.

## Məlumat və açarlar

- Windows: `%APPDATA%\DeYaz` və `%LOCALAPPDATA%\DeYaz`
- macOS/Linux: `$XDG_CONFIG_HOME/deyaz` və `$XDG_DATA_HOME/deyaz`
- OpenRouter açarı OS keychain/credential store-da saxlanılır.
- Köhnə `Dikte` ayarları ilk açılışda yeni qovluğa **kopyalanır**, silinmir.
- Audio yalnız “saxla” seçimi aktivdirsə qalır; tarixçə lokal saxlanılır.

## Open source

Contributions xoşdur. Başlamazdan əvvəl [CONTRIBUTING.md](CONTRIBUTING.md),
arxitektura üçün [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), model yoxlamaları
üçün [docs/EVALUATION.md](docs/EVALUATION.md) fayllarına bax.

GPL-3.0-or-later. Layihənin başlanğıc nöqtəsi `yusufipk/dikte` olub; attribution
və lisenziya məlumatları [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)-dədir.
