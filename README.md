# Dikte for Windows

<p align="center">
  <img src="assets/dikte-logo.png" width="150" alt="Dikte logo">
</p>

Windows 10/11 üçün native səsdən-mətn tətbiqi. İstənilən proqramda qlobal qısa
yolla danışığı yazır, mətni təmizləyir və aktiv input sahəsinə əlavə edir.

## Əsas imkanlar

- Daşına bilən, animasiyalı mini mikrofon düyməsi
- Sistem tray menyusu və `Ctrl+Alt+R` qlobal qısa yolu
- Azərbaycan, ingilis və türk dillərində transkripsiya
- OpenAI və ya OpenRouter speech-to-text provider-i
- Prompt Engineer, Cover Letter, Email, Social və başqa rəngli iş modları
- Aktiv pəncərə və layihədən avtomatik kontekst toplama
- Son nəticələr, bir kliklə kopyalama və clipboard-a avtomatik əlavə etmə
- Audio/video fayllarının transkripsiyası, təmizlənməsi və xülasəsi
- MP3, WAV, M4A, MP4, MKV, WEBM, MOV və AVI dəstəyi
- TXT və zaman damğalı SRT ixracı

## İşə salmaq

### Hazır EXE

GitHub Actions build artifact-ından `Dikte.exe` faylını endir və aç. Video
transkripsiyası üçün [FFmpeg](https://ffmpeg.org/download.html) sistem `PATH`-ında
olmalıdır.

### Mənbə kodundan

```powershell
git clone https://github.com/hasan0v/dikte-windows.git
cd dikte-windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python dikte_windows.py
```

və ya `Dikte-baslat.bat` faylına iki dəfə kliklə.

## İlkin ayarlar

`Ayarlar → API & Models` bölməsində:

1. OpenAI və ya OpenRouter API açarını əlavə et.
2. Transkripsiya provider və modelini seç.
3. Təmizləmə, xülasə və xüsusi iş modları üçün OpenRouter açarı daxil et.

Açarlar yalnız lokal olaraq `%APPDATA%\Dikte\config.json` faylında saxlanılır.
Tarixçə `%LOCALAPPDATA%\Dikte` qovluğundadır. Köhnə versiyanın məlumatları ilk
açılışda avtomatik köçürülür.

## File Transcribe

`Ayarlar → File Transcribe` bölməsində audio və ya video seç:

- danışığın dilini avtomatik və ya manual təyin et;
- nəticəni orijinal dildə və ya Azərbaycanca hazırla;
- tam transkript, qısa/ətraflı xülasə, görüş qeydləri, action items və ya dərs
  qeydləri seç;
- lazım olduqda xüsusi fokus yaz;
- nəticəni clipboard-a, TXT-yə və ya SRT-yə çıxar.

Uzun media faylları avtomatik hissələrə bölünür. Video səsi API üçün mono 16 kHz
audioya çevrilir.

## EXE build

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean Dikte.spec
```

Nəticə `dist\Dikte.exe` ünvanında yaranır.

## Layihə quruluşu

- `dikte_windows.py` — əsas PyQt6 UI, tray, qlobal hotkey və mini HUD
- `filetranscribe.py` — media çevirmə, bölmə, transkripsiya və xülasə axını
- `project_context.py` — aktiv Windows tətbiqi və layihə konteksti
- `work_modes.py` — rəngli AI iş modları və sistem promptları
- `api.py` — OpenAI/OpenRouter sorğuları
- `config.py` — lokal ayarlar və tarixçə

## Məxfilik

Səs yalnız seçilmiş transkripsiya provider-inə göndərilir. Təmizləmə və xülasə
aktivdirsə transkript seçilmiş OpenRouter modelinə göndərilir. API açarları
repoya daxil edilmir.

## Lisenziya və mənşə

GPL-3.0-or-later. Bu Windows versiyası yusufipk/dikte layihəsindən törəyib,
amma ayrıca repo, Windows UI, build və runtime axını ilə müstəqil inkişaf edir.
Ətraflı məlumat üçün [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) faylına bax.
