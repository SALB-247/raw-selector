# Supported hardware

What this tool can read, correct, and colour-calibrate. Auto-generated
from the bundled databases — see the linked sources for the authority.

## RAW / image formats

RAW decoding is handled by **LibRaw 0.22** (via rawpy). The formats regularly exercised are **ARW · CR3 · CR2 · NEF · RAF · ORF · RW2 · DNG**; LibRaw opens ~20 in total. **JPEG** (`.jpg` `.jpeg`) and **HEIF** (`.hif` `.heic` `.heif`) are also judged and developed directly — HEIF via `pillow-heif` (libheif).

> Nikon Z-series **High Efficiency (HE/HE\*)** compression is intoPIX TicoRAW, which no open decoder (LibRaw/dcraw/darktable/RawTherapee) can unpack — pixels are unavailable, but metadata (white balance) is still read so the develop panel's temperature control keeps working.

## Camera colour calibration (built-in-JPEG based)

The tool can measure per-camera colour corrections by comparing a folder's RAW frames against the camera's own JPEGs. For bodies LibRaw doesn't colour-manage well, this recovers the intended look. The result is **editable and removable** in the develop panel's Optics section (Camera colour calibration), and a fresh measurement overrides it.

Shipped calibrations:

- **Canon EOS R6 Mark III** — built-in default, from 10 JPEG reference frames. Adjustable / removable.

For any other body, open a folder of its RAWs and the app offers to measure one on the spot.

## Lens correction — lensfun

Distortion / vignetting correction uses the **lensfun** database (bundled snapshot): **1508 lens profiles** across **80 makers**. Source and updates: https://github.com/lensfun/lensfun (CC-BY-SA 3.0).

Not in the list? Manual distortion / vignetting / defringe still works, and you can drop extra lensfun `.xml` files into the user lens folder (develop panel → Optics → "Lens profile folder") without rebuilding.

<details><summary><b>7Artisans</b> (5)</summary>

- 7Artisans 18mm f/5.6 Full Frame
- 7Artisans 18mm f/6.3 II
- 7Artisans 35mm f/0.95
- 7Artisans 35mm f/1.4 APS-C (manual)
- 7Artisans 60mm f/2.8 II APS-C

</details>

<details><summary><b>AEE</b> (1)</summary>

- AEE SD19 & compatibles

</details>

<details><summary><b>Apple</b> (2)</summary>

- iPhone XS back camera 4.25mm f/1.8 & compatibles
- iPhone XS back camera 6mm f/2.4 & compatibles

</details>

<details><summary><b>Arsenal</b> (1)</summary>

- MC Volna-3 80mm f/2.8

</details>

<details><summary><b>AstrHori</b> (1)</summary>

- AstrHori 10mm f/8

</details>

<details><summary><b>BelOMO</b> (1)</summary>

- MC Peleng 3.5/8

</details>

<details><summary><b>Beroflex</b> (2)</summary>

- Beroflex 1:8 500mm
- Beroflex 400mm f/6.3

</details>

<details><summary><b>Canon</b> (254)</summary>

- Canon DIGITAL IXUS 400 & compatibles (Standard)
- Canon DIGITAL IXUS 400 & compatibles, with Tiffen MegaPlus 0.56 converter
- Canon DIGITAL IXUS 80 IS & compatibles (Standard)
- Canon DIGITAL IXUS II & compatibles (Standard)
- Canon DIGITAL IXUS i & compatibles (Standard)
- Canon EF 100-200mm f/4.5A
- Canon EF 100-300mm f/4.5-5.6 USM
- Canon EF 100-300mm f/5.6L
- Canon EF 100-400mm f/4.5-5.6L IS II USM
- Canon EF 100-400mm f/4.5-5.6L IS II USM + 1.4x extender
- Canon EF 100-400mm f/4.5-5.6L IS USM
- Canon EF 100mm f/2.8 Macro
- Canon EF 100mm f/2.8 Macro USM
- Canon EF 100mm f/2.8L Macro IS USM
- Canon EF 11-24mm f/4L USM
- Canon EF 135mm f/2.8 Soft Focus
- Canon EF 135mm f/2L
- Canon EF 14mm f/2.8L II USM
- Canon EF 15mm f/2.8 Fisheye
- Canon EF 16-35mm f/2.8L II USM
- Canon EF 16-35mm f/2.8L III USM
- Canon EF 16-35mm f/2.8L USM
- Canon EF 16-35mm f/4L IS USM
- Canon EF 17-35mm f/2.8L USM
- Canon EF 17-40mm f/4L USM
- Canon EF 20-35mm f/3.5-4.5 USM
- Canon EF 200mm f/2.8L II USM
- Canon EF 200mm f/2.8L USM
- Canon EF 20mm f/2.8 USM
- Canon EF 22-55mm f/4-5.6 USM
- Canon EF 24-105mm f/3.5-5.6 IS STM
- Canon EF 24-105mm f/4L IS II USM
- Canon EF 24-105mm f/4L IS USM
- Canon EF 24-70mm f/2.8L II USM
- Canon EF 24-70mm f/2.8L USM
- Canon EF 24-70mm f/4L IS USM
- Canon EF 24-85mm f/3.5-4.5 USM
- Canon EF 24mm f/1.4L II USM
- Canon EF 24mm f/1.4L USM
- Canon EF 24mm f/2.8
- Canon EF 24mm f/2.8 IS USM
- Canon EF 28-105mm f/3.5-4.5 II USM
- Canon EF 28-135mm f/3.5-5.6 IS USM
- Canon EF 28-300mm f/3.5-5.6L IS USM
- Canon EF 28-70mm f/2.8L USM
- Canon EF 28-80mm f/3.5-5.6 USM
- Canon EF 28-80mm f/3.5-5.6 USM IV
- Canon EF 28-90mm f/4-5.6 USM
- Canon EF 28mm f/1.8
- Canon EF 28mm f/1.8 USM
- Canon EF 28mm f/2.8
- Canon EF 300mm f/2.8L IS II USM
- Canon EF 300mm f/2.8L IS II USM + EF 1.4x extender III
- Canon EF 300mm f/2.8L IS II USM + EF 2.0x extender III
- Canon EF 300mm f/2.8L IS USM
- Canon EF 300mm f/2.8L IS USM + 1.4x extender
- Canon EF 300mm f/2.8L IS USM + 2x extender
- Canon EF 300mm f/2.8L IS USM +1.4x extender III
- Canon EF 300mm f/2.8L IS USM +2x III extender
- Canon EF 300mm f/4L IS USM
- Canon EF 300mm f/4L IS USM + 1.4x extender
- Canon EF 35-105mm f/3.5-4.5
- Canon EF 35-105mm f/4.5-5.6
- Canon EF 35-135mm f/4-5.6 USM
- Canon EF 35-70mm f/3.5-4.5
- Canon EF 35-80mm f/4-5.6 III
- Canon EF 35mm f/1.4L USM
- Canon EF 35mm f/2
- Canon EF 35mm f/2 IS USM
- Canon EF 400mm f/5.6L USM
- Canon EF 400mm f/5.6L USM + 1.4x extender
- Canon EF 40mm f/2.8 STM
- Canon EF 50-200mm f/3.5-4.5L
- Canon EF 500mm f/4L IS II USM
- Canon EF 500mm f/4L IS II USM + EF 1.4x extender III
- Canon EF 500mm f/4L IS II USM + EF 2.0x extender III
- Canon EF 50mm f/1.2L USM
- Canon EF 50mm f/1.4 USM
- Canon EF 50mm f/1.8
- Canon EF 50mm f/1.8 MkII
- Canon EF 50mm f/1.8 STM
- Canon EF 50mm f/2.5 Compact Macro
- Canon EF 55-200mm f/4.5-5.6
- Canon EF 70-200mm f/2.8L IS II USM
- Canon EF 70-200mm f/2.8L IS II USM + EF 2x extender III
- Canon EF 70-200mm f/2.8L IS USM
- Canon EF 70-200mm f/2.8L IS USM + EF 1.4x extender
- Canon EF 70-200mm f/2.8L IS USM + EF 2x II extender
- Canon EF 70-200mm f/2.8L USM
- Canon EF 70-200mm f/4L IS USM
- Canon EF 70-200mm f/4L USM
- Canon EF 70-200mm f/4L USM + EF 1.4x extender
- Canon EF 70-210mm f/3.5-4.5 USM
- Canon EF 70-210mm f/4
- Canon EF 70-300mm f/4-5.6 IS II USM
- Canon EF 70-300mm f/4-5.6 IS USM
- Canon EF 70-300mm f/4-5.6L IS USM
- Canon EF 70-300mm f/4.5-5.6 DO IS USM
- Canon EF 75-300mm F4-5.6 III
- Canon EF 75-300mm f/4-5.6 III USM
- Canon EF 75-300mm f/4-5.6 IS USM
- Canon EF 8-15mm f/4L Fisheye USM
- Canon EF 80-200mm f/2.8L
- Canon EF 80-200mm f/4.5-5.6
- Canon EF 85mm f/1.2L II USM
- Canon EF 85mm f/1.2L USM
- Canon EF 85mm f/1.4L IS USM
- Canon EF 85mm f/1.8 USM
- Canon EF 90-300mm f/4.5-5.6
- Canon EF-M 11-22mm f/4-5.6 IS STM
- Canon EF-M 15-45mm f/3.5-6.3 IS STM
- Canon EF-M 18-55mm f/3.5-5.6 IS STM
- Canon EF-M 22mm f/2 STM
- Canon EF-M 28mm f/3.5 Macro IS STM
- Canon EF-M 32mm f/1.4 STM
- Canon EF-M 55-200mm f/4.5-6.3 IS STM
- Canon EF-S 10-18mm f/4.5-5.6 IS STM
- Canon EF-S 10-22mm f/3.5-4.5 USM
- Canon EF-S 15-85mm f/3.5-5.6 IS USM
- Canon EF-S 17-55mm f/2.8 IS USM
- Canon EF-S 17-85mm f/4-5.6 IS USM
- Canon EF-S 18-135mm f/3.5-5.6 IS
- Canon EF-S 18-135mm f/3.5-5.6 IS STM
- Canon EF-S 18-135mm f/3.5-5.6 IS USM
- Canon EF-S 18-200mm f/3.5-5.6 IS
- Canon EF-S 18-55mm f/3.5-5.6
- Canon EF-S 18-55mm f/3.5-5.6 II
- Canon EF-S 18-55mm f/3.5-5.6 III
- Canon EF-S 18-55mm f/3.5-5.6 IS
- Canon EF-S 18-55mm f/3.5-5.6 IS II
- Canon EF-S 18-55mm f/3.5-5.6 IS STM
- Canon EF-S 24mm f/2.8 STM
- Canon EF-S 55-250mm f/4-5.6 IS
- Canon EF-S 55-250mm f/4-5.6 IS II
- Canon EF-S 55-250mm f/4-5.6 IS STM
- Canon EF-S 60mm f/2.8 Macro USM
- Canon FD 200mm f/2.8 S.S.C.
- Canon FD 50mm f/1.4 S.C.C.
- Canon FD 50mm f/1.4 S.S.C.
- Canon FDn 100mm 1:2.8
- Canon FDn 100mm f/2.8
- Canon FDn 135mm 1:2.8
- Canon FDn 200mm 1:4
- Canon FDn 24mm 1:2.8
- Canon FDn 28mm f/2.8
- Canon FDn 50mm 1:1.4
- Canon FDn 50mm 1:1.8
- Canon G7 X & compatibles
- Canon IXUS 125 HS & compatibles
- Canon IXUS 220 HS & compatibles, with CHDK's DNG
- Canon IXY 220F & compatibles
- Canon Lens FL 135mm F3.5
- Canon Lens FL 50mm F1.4
- Canon PowerShot A1200 & compatibles (Standard)
- Canon PowerShot A4000 IS & compatibles, with CHDK's DNG
- Canon PowerShot A495 & compatibles, with CHDK's DNG
- Canon PowerShot A510 & compatibles (Standard)
- Canon PowerShot A610 & compatibles (Standard)
- Canon PowerShot A610 & compatibles, with CHDK's DNG
- Canon PowerShot A640 & compatibles
- Canon PowerShot A640 & compatibles, with WC-DC58N
- Canon PowerShot A650 IS & compatibles (Standard)
- Canon PowerShot A70 & compatibles (Standard)
- Canon PowerShot A720 IS & compatibles (Standard)
- Canon PowerShot A80 & compatibles (Standard)
- Canon PowerShot A80 & compatibles, with Tiffen 0.56x converter
- Canon PowerShot A80 & compatibles, with Tiffen 2x converter
- Canon PowerShot G1 & compatibles (Standard)
- Canon PowerShot G1 X Mark III & compatibles (Standard)
- Canon PowerShot G11 & compatibles (Standard)
- Canon PowerShot G12 & compatibles (Standard)
- Canon PowerShot G15 & compatibles (Standard)
- Canon PowerShot G16 & compatibles
- Canon PowerShot G1X & compatibles (Standard)
- Canon PowerShot G1X Mark II & compatibles
- Canon PowerShot G2 & compatibles (Standard)
- Canon PowerShot G2 & compatibles, with Geobartic 0.5x converter
- Canon PowerShot G3 & compatibles (Standard)
- Canon PowerShot G3 & compatibles, with WC-DC58N
- Canon PowerShot G3 X & compatibles
- Canon PowerShot G7 & compatibles (Standard)
- Canon PowerShot G9 X & compatibles
- Canon PowerShot Pro1 & compatibles (Standard)
- Canon PowerShot Pro90 IS & compatibles (Standard)
- Canon PowerShot S1 IS & compatibles (Standard)
- Canon PowerShot S100 & compatibles
- Canon PowerShot S110 & compatibles
- Canon PowerShot S120 & compatibles
- Canon PowerShot S2 IS & compatibles (Standard)
- Canon PowerShot S2 IS & compatibles, with TC-DC58B
- Canon PowerShot S30 & compatibles (Standard)
- Canon PowerShot S5 IS & compatibles (Standard)
- Canon PowerShot S5 IS & compatibles, with TC-DC58B
- Canon PowerShot S70 & compatibles (Standard)
- Canon PowerShot S70 & compatibles, with TC-DC10 2x converter
- Canon PowerShot S90 & compatibles (Standard)
- Canon PowerShot S95 & compatibles
- Canon PowerShot SD200 & compatibles (Standard)
- Canon PowerShot SD500 & compatibles (Standard)
- Canon PowerShot SD950 IS & compatibles (Standard)
- Canon PowerShot SX10 IS
- Canon PowerShot SX150 IS & compatibles (Standard)
- Canon PowerShot SX150 IS & compatibles, with CHDK's DNG
- Canon PowerShot SX160 IS & compatibles, with CHDK's DNG
- Canon PowerShot SX220 HS & compatibles (Standard)
- Canon PowerShot SX220 HS & compatibles, with CHDK's DNG
- Canon PowerShot SX260 HS & compatibles, with CHDK's DNG
- Canon PowerShot SX30 IS & compatibles, with CHDK's DNG
- Canon PowerShot SX50 HS & compatibles
- Canon PowerShot SX510 HS & compatibles
- Canon PowerShot SX510 HS & compatibles, with CHDK's DNG
- Canon PowerShot SX60 HS & compatibles
- Canon PowerShot SX710 HS & compatibles, with CHDK's DNG
- Canon RF 10-20mm f/4L IS STM
- Canon RF 100-400mm F5.6-8 IS USM
- Canon RF 100-500mm F4.5-7.1L IS USM
- Canon RF 100mm F2.8L Macro IS USM
- Canon RF 135mm F1.8L IS USM
- Canon RF 14-35mm F4 L IS USM
- Canon RF 15-30mm F4.5-6.3 IS STM
- Canon RF 15-35mm F2.8L IS USM
- Canon RF 16mm F2.8 STM
- Canon RF 200-800mm F6.3-9 IS USM
- Canon RF 24-105mm F4-7.1 IS STM
- Canon RF 24-105mm F4L IS USM
- Canon RF 24-240mm F4-6.3 IS USM
- Canon RF 24-50mm F4.5-6.3 IS STM
- Canon RF 24-70mm F2.8L IS USM
- Canon RF 24mm F1.4 L VCM
- Canon RF 24mm F1.8 MACRO IS STM
- Canon RF 28-70mm F2 L USM
- Canon RF 28-70mm F2.8 IS STM
- Canon RF 28mm F2.8 STM
- Canon RF 35mm F1.4 L VCM
- Canon RF 35mm F1.8 MACRO IS STM
- Canon RF 45mm F1.2 STM
- Canon RF 50mm F1.2 L USM
- Canon RF 50mm F1.8 STM
- Canon RF 600mm F11 IS STM
- Canon RF 70-200mm F2.8L IS USM
- Canon RF 70-200mm F4L IS USM
- Canon RF 800mm F11 IS STM
- Canon RF 85mm F1.2L USM
- Canon RF 85mm F2 MACRO IS STM
- Canon RF-S 10-18mm F4.5-6.3 IS STM
- Canon RF-S 18-150mm F3.5-6.3 IS STM
- Canon RF-S 18-45mm F4.5-6.3 IS STM
- Canon RF-S55-210mm F5-7.1 IS STM
- Canon TS-E 24mm f/3.5L
- Canon TS-E 45mm f/2.8
- Canon TS-E 90mm f/2.8
- EF-S18-55mm f/4-5.6 IS STM
- EF35mm f/1.4L II USM (750)
- G5 X Mark II & compatibles

</details>

<details><summary><b>Carl Zeiss</b> (29)</summary>

- DSC-F707 & compatibles (Standard)
- DSC-F707 & compatibles, macro at 1 inch lens-to-subject
- DSC-F707 & compatibles, macro at 12 inches lens-to-subject
- DSC-F707 & compatibles, macro at 16 inches lens-to-subject
- DSC-F707 & compatibles, macro at 2 inches lens-to-subject
- DSC-F707 & compatibles, macro at 20 inches lens-to-subject
- DSC-F707 & compatibles, macro at 24 inches lens-to-subject
- DSC-F707 & compatibles, macro at 32 inches lens-to-subject
- DSC-F707 & compatibles, macro at 4 inches lens-to-subject
- DSC-F707 & compatibles, macro at 6 inches lens-to-subject
- DSC-F707 & compatibles, macro at 8 inches lens-to-subject
- DSC-F707 & compatibles, with Sakar 1858W
- DSC-F707 & compatibles, with VCL-HGD0758 wide angle
- DSC-F828 & compatibles (Standard)
- DSC-F828 & compatibles, at 2 feet lens-to-subject
- DSC-H1 & compatibles (Standard)
- DSC-H1 & compatibles, with VCL-DH0758 0.7x converter
- DSC-H1 & compatibles, with VCL-DH1758 1.7x converter
- DSC-R1 & compatibles (Standard)
- DSC-RX1R & compatibles
- DSC-RX1RM2 & compatibles
- DSC-S60 & compatibles (Standard)
- DSC-S85 & compatibles (Standard)
- DSC-S85 & compatibles, with VCL-MHG07a converter
- DSC-T1 & compatibles (Standard)
- DSC-V1 & compatibles (Standard)
- DSC-V1 & compatibles, with VCL-DEH07V converter
- DSC-V1 & compatibles, with VCL-DEH17V converter
- DSC-W1 & compatibles (Standard)

</details>

<details><summary><b>Casio</b> (6)</summary>

- EX-P600 & compatibles (Standard)
- EX-P600 & compatibles, with WC-DC58A
- EX-Z4 & compatibles (Standard)
- EX-Z750 & compatibles (Standard)
- QV-3500EX & compatibles (Standard)
- QV-3500EX & compatibles, with Raynox 0.66x

</details>

<details><summary><b>Chinon</b> (2)</summary>

- Auto Chinon 35mm f/2.8
- Chinon 75-205mm f/3.8

</details>

<details><summary><b>Contax</b> (3)</summary>

- Contax G Planar T* 2/35
- Zeiss 21mm f/2.8 Distagon
- Zeiss 28mm f/2.8 Distagon

</details>

<details><summary><b>Cosina</b> (7)</summary>

- 24mm 1:2.0 Macro
- 24mm 2.0 Macro
- Cosina 19-35mm f/3.5-4.5 MC
- Cosina Cosinon-S 50mm 1:2
- Cosinon-T 135mm 1:3.5
- Cosinon-W 28mm 1:2.8
- Cosinon-W 35mm 1:2.8

</details>

<details><summary><b>DJI</b> (9)</summary>

- DJI MFT 15mm F1.7 ASPH
- FC2103 & compatibles
- FC3411 & compatibles
- FC3582 & compatibles
- FC6310 & compatibles
- FC6310R & compatibles
- Mavic Pro FC220 & compatibles
- Phantom 3 Pro
- Phantom Vision FC200 & compatibles

</details>

<details><summary><b>Fotasy</b> (1)</summary>

- Fotasy M3517 35mm f/1.7

</details>

<details><summary><b>Fujian</b> (1)</summary>

- 35mm f/1.7

</details>

<details><summary><b>Fujifilm</b> (72)</summary>

- FinePix 2800 ZOOM & compatibles (Standard)
- FinePix F11 & compatibles (Standard)
- FinePix F200EXR & compatibles (Standard)
- FinePix F601 ZOOM & compatibles (Standard)
- FinePix F770EXR & compatibles (Standard)
- FinePix F810 & compatibles (Standard)
- FinePix F810 & compatibles, with WL-FXE01 (full wide)
- FinePix HS20EXR & compatibles (Standard)
- FinePix S5500 & compatibles (Standard)
- FinePix S5600
- FinePix S602 ZOOM & compatibles (Standard)
- FinePix S9000 & compatibles (Standard)
- Fujifilm FinePix A370
- Fujifilm X-S1
- Fujifilm XQ1
- GF110mmF2 R LM WR
- GF23mmF4 R LM WR
- GF35-70mmF4.5-5.6 WR
- GF45mmF2.8 R WR
- GF50mmF3.5 R LM WR
- GF55mmF1.7 R WR
- GF80mmF1.7 R WR
- GFX100RF & compatibles (Standard)
- X10 & compatibles (Standard)
- X100 & compatibles (Standard)
- X100 & compatibles (Standard) + TCL-X100
- X100 & compatibles, with TCL-X100
- X100V & compatibles
- X100V & compatibles (Standard)
- X100V & compatibles, with TCL-X100
- X100V & compatibles, with WCL-X100
- X70 & compatibles (Standard)
- XC15-45mmF3.5-5.6 OIS PZ
- XC16-50mmF3.5-5.6 OIS
- XC16-50mmF3.5-5.6 OIS II
- XC35mmF2
- XC50-230mmF4.5-6.7 OIS
- XC50-230mmF4.5-6.7 OIS II
- XF10 & compatibles (Standard)
- XF10-24mmF4 R OIS
- XF10-24mmF4 R OIS WR
- XF100-400mmF4.5-5.6 R LM OIS WR
- XF100-400mmF4.5-5.6 R LM OIS WR + 1.4x converter
- XF14mmF2.8 R
- XF16-50mmF2.8-4.8 R LM WR
- XF16-55mmF2.8 R LM WR
- XF16-55mmF2.8 R LM WR II
- XF16-80mmF4 R OIS WR
- XF16mmF1.4 R WR
- XF16mmF2.8 R WR
- XF18-135mmF3.5-5.6R LM OIS WR
- XF18-55mmF2.8-4 R LM OIS
- XF18mmF2 R
- XF23mmF1.4 R
- XF23mmF1.4 R LM WR
- XF23mmF2 R WR
- XF27mmF2.8
- XF27mmF2.8 R WR
- XF30mmF2.8 R LM WR Macro
- XF33mmF1.4 R LM WR
- XF35mmF1.4 R
- XF35mmF2 R WR
- XF50-140mmF2.8 R LM OIS WR
- XF50mmF2 R WR
- XF55-200mmF3.5-4.8 R LM OIS
- XF56mmF1.2 R
- XF56mmF1.2 R APD
- XF56mmF1.2 R WR
- XF60mmF2.4 R Macro
- XF70-300mmF4-5.6 R LM OIS WR
- XF70-300mmF4-5.6 R LM OIS WR + 1.4x
- XF90mmF2 R LM WR

</details>

<details><summary><b>Generic</b> (3)</summary>

- Fisheye 8-20mm f/1.0
- Panoramic 10-100mm f/1.0
- Rectilinear 10-1000mm f/1.0

</details>

<details><summary><b>GitUp</b> (1)</summary>

- Git2 & compatibles

</details>

<details><summary><b>GoPro</b> (6)</summary>

- GoPro Hero3+ black & compatibles
- HD2 & compatibles
- HERO10 Black & compatibles
- HERO11 Black & compatibles
- HERO4
- HERO4 black

</details>

<details><summary><b>Hasselblad</b> (2)</summary>

- L1D-20c & compatibles
- L2D-20c & compatibles

</details>

<details><summary><b>Honor</b> (1)</summary>

- Honor 6A & compatibles

</details>

<details><summary><b>Huawei</b> (3)</summary>

- Huawei P10 Lite & compatibles
- Huawei P20 Pro & compatibles
- P30 Pro

</details>

<details><summary><b>Irix</b> (2)</summary>

- Irix 11mm f/4 G
- Irix 15mm f/2.4

</details>

<details><summary><b>Kipon</b> (1)</summary>

- Elegant 35mm F/2.4

</details>

<details><summary><b>KMZ</b> (13)</summary>

- Helios-40 85mm f/1.5
- Helios-44 58mm 1:2
- Industar-50-2 3.5/50
- Jupiter-37AM MC 3.5/135
- MC APO Telezenitar 2.8/135
- MC APO Telezenitar 4.5/300
- MC Helios-44M-4 58mm 1:2
- MC MTO 11CA 10/1000
- MC Mir-20M 3.5/20
- MC Variozenitar-K 2.8-3.5/25-45
- MC Zenitar 2.8/16
- MC Zenitar 2/50
- MIR-1B 37mm f/2.8

</details>

<details><summary><b>Kodak</b> (1)</summary>

- Kodak CX6330 & compatibles

</details>

<details><summary><b>Konica Minolta</b> (15)</summary>

- DiMAGE 7 & compatibles (Standard)
- DiMAGE 7 & compatibles, with ACW-100 converter
- DiMAGE G400 & compatibles (Standard)
- DiMAGE X & compatibles (Standard)
- DiMAGE Z1 & compatibles (Standard)
- DiMAGE Z1 & compatibles, with ZCW-100
- DiMAGE Z10 & compatibles (Standard)
- DiMAGE Z10 & compatibles, with ZCW-200
- DiMAGE Z2 & compatibles (Standard)
- DiMAGE Z2 & compatibles, with ZCW-100
- DiMAGE Z3 & compatibles (Standard)
- KM 20mm f/2.8
- KM 24-105mm f/3.5-4.5 AF D
- KM 28-100mm f/3.5-5.6 AF D
- KM 80-200mm f/2.8

</details>

<details><summary><b>Leica</b> (21)</summary>

- DMC-FX7 & compatibles (Standard)
- DMC-FZ10 & compatibles (Standard)
- DMC-FZ200 & compatibles (Standard)
- DMC-FZ28 & compatibles (Standard)
- DMC-FZ3 & compatibles (Standard)
- DMC-FZ30 & compatibles (Standard)
- DMC-FZ5 & compatibles (Standard)
- DMC-LX1 & compatibles (Standard)
- DMC-LX10 & compatibles (Standard)
- DMC-LX100 & compatibles
- DMC-LX3 & compatibles (Standard)
- DMC-LX5 & compatibles (Standard)
- DMC-LX7 & compatibles (Standard)
- DMC-LZ2 & compatibles (Standard)
- DMC-TZ100 & compatibles
- DMC-TZ70 & compatibles
- Digilux 2 & compatibles (Standard)
- FZ1000 & compatibles
- FZ150 & compatibles
- FZ2000 & compatibles
- Leica X Vario 18.0-46.0 mm f/3.5-6.4

</details>

<details><summary><b>LEICA CAMERA AG</b> (3)</summary>

- APO-Summicron 1:2/43 Asph.
- LEICA Q (Typ 116) & compatibles
- LEICA Q2 & compatibles

</details>

<details><summary><b>Leica Camera AG</b> (14)</summary>

- APO-Summicron-M 1:2/75 Asph.
- APO-Summicron-SL 1:2/35 Asph.
- APO-Summicron-SL 1:2/50 Asph.
- Elmarit-M 1:2.8/28 Asph.
- Elmarit-M 1:2.8/90
- Elmarit-TL 1:2.8/18 Asph.
- Summicron TL 1:2 23 ASPH.
- Summicron-M 1:2/28 Asph.
- Summicron-M 1:2/35 Asph.
- Summicron-M 1:2/50
- Summicron-TL 1:2/23 Asph.
- Summilux-TL 1:1.4/35 Asph.
- Vario-Elmarit-SL 1:2.8-4/24-90 ASPH.
- Vario-Elmarit-SL 1:2.8/24-70 Asph.

</details>

<details><summary><b>LG</b> (1)</summary>

- LG G4 & compatibles

</details>

<details><summary><b>LZOS</b> (1)</summary>

- Industar-61 L/Z MC 50mm f/2.8

</details>

<details><summary><b>Mamiya</b> (7)</summary>

- 120mm f/32.0-4.0
- 150mm f/32.0-3.5
- 35mm f/22.0-3.5
- Mamiya 35mm f/3.5
- Mamiya 55-110mm f/4.5
- Mamiya 80mm f/2.8
- Mamiya/Sekor SX 55mm f/1.8

</details>

<details><summary><b>Meike</b> (7)</summary>

- MEKE SL 35mm F1.8 STM PRO
- Meike 25mm f/1.8
- Meike 28mm f/2.8
- Meike 35mm f/1.7
- Meike 50mm F1.2
- Meike 50mm f/2.0
- Meike 85mm f/1.8

</details>

<details><summary><b>Meke</b> (2)</summary>

- MEKE SL 85mm F1.8 STM PRO
- Meke FF 35mm f/2.0

</details>

<details><summary><b>Meyer Optik Görlitz</b> (1)</summary>

- Meyer Optik Görlitz 3.5/30mm

</details>

<details><summary><b>Meyer-Optik Görlitz</b> (8)</summary>

- Domiplan 50mm f/2.8
- Helioplan 40mm f/4.5
- Lydith 30mm f/3.5
- Orestegon 29mm f/2.8
- Orestegor 200mm f/4
- Oreston 50mm f/1.8
- Orestor 100mm f/2.8
- Orestor 135mm f/2.8

</details>

<details><summary><b>Minolta</b> (21)</summary>

- Minolta AF 100-300mm f/4.5-5.6 APO (D)
- Minolta AF 135mm f/2.8
- Minolta AF 17-35mm F2.8-4 (D)
- Minolta AF 17-35mm f/2.8-4 (D)
- Minolta AF 28-75mm F2.8 (D)
- Minolta AF 35-105mm f/3.5-4.5
- Minolta AF 35-70mm F4
- Minolta AF 50mm f/1.4
- Minolta AF 50mm f/1.7
- Minolta AF 50mm f/2.8 Macro
- Minolta AF 70-210mm f/4 Macro
- Minolta AF 85mm f/1.4G (D)
- Minolta MC Rokkor-PG 50mm 1:1.4
- Minolta MD 135mm f/2.8
- Minolta MD 24mm f/2.8
- Minolta MD 28mm f/2.8
- Minolta MD 35mm 1/2.8
- Minolta MD 45mm f/2
- Minolta MD 50mm f/1.4
- Minolta MD Rokkor 50mm 1:1.4
- Minolta/Sony AF 24-105mm f/3.5-4.5 (D)

</details>

<details><summary><b>Miranda</b> (1)</summary>

- Miranda 28mm f/2.8 MC

</details>

<details><summary><b>Mitakon</b> (2)</summary>

- Mitakon Speedmaster 50mm f/0.95 III
- Mitakon wide MC f=24mm 1:2.8

</details>

<details><summary><b>MTO</b> (1)</summary>

- MTO-500 500mm f/8 mirror lens

</details>

<details><summary><b>Nikon</b> (218)</summary>

- 1 Nikkor 10mm f/2.8
- 1 Nikkor 18.5mm f/1.8
- 1 Nikkor 32mm f/1.2
- 1 Nikkor AW 10mm f/2.8
- 1 Nikkor AW 11-27.5mm f/3.5-5.6
- 1 Nikkor VR 10-30mm f/3.5-5.6
- 1 Nikkor VR 30-110mm f/3.8-5.6
- 200-500mm F5.6 174
- Coolpix A & compatibles
- Coolpix P330 & compatibles (Standard)
- Coolpix P60 & compatibles (Standard)
- Coolpix P7000 & compatibles
- Coolpix P7800 & compatibles
- Coolpix S3300 & compatibles
- E4800 & compatibles (Standard)
- E5000 & compatibles (Standard)
- E5000 & compatibles, with WC-E68 (full wide)
- E5400 & compatibles (Standard)
- E5400 & compatibles, with WC-E80
- E5700 & compatibles (Standard)
- E5700 & compatibles, with TC-E15ED (full tele)
- E5700 & compatibles, with WC-E80 (full wide)
- E7900 & compatibles (Standard)
- E8400 & compatibles (Standard)
- E8400 & compatibles, with WC-E75
- E8800 & compatibles (Standard)
- E8800 & compatibles, with WM-E80
- E950 & compatibles (Standard)
- E990 & compatibles (Standard)
- E990 & compatibles, with WC-E63
- E995 & compatibles (Standard)
- E995 & compatibles, with TC-E2
- E995 & compatibles, with WC-E24
- E995 & compatibles, with WC-E63
- Nikkor 55mm f/3.5 Micro
- Nikkor AI-S 85mm f/2.0
- Nikkor Z 100-400mm f/4.5-5.6 VR S
- Nikkor Z 135mm f/1.8 S Plena
- Nikkor Z 14-24mm f/2.8 S
- Nikkor Z 14-30mm f/4 S
- Nikkor Z 180-600mm f/5.6-6.3 VR
- Nikkor Z 20mm f/1.8 S
- Nikkor Z 24-120mm f/4 S
- Nikkor Z 24-200mm f/4-6.3 VR
- Nikkor Z 24-70mm f/2.8 S
- Nikkor Z 24-70mm f/4 S
- Nikkor Z 26mm f/2.8
- Nikkor Z 28-400mm f/4-8 VR
- Nikkor Z 28-75mm f/2.8
- Nikkor Z 28mm f/2.8
- Nikkor Z 35mm f/1.4
- Nikkor Z 35mm f/1.8 S
- Nikkor Z 400mm f/4.5 VR S
- Nikkor Z 40mm f/2
- Nikkor Z 50mm f/1.2 S
- Nikkor Z 50mm f/1.4
- Nikkor Z 50mm f/1.8 S
- Nikkor Z 600mm f/6.3 VR S
- Nikkor Z 70-180mm f/2.8
- Nikkor Z 70-200mm f/2.8 VR S
- Nikkor Z 85mm f/1.2 S
- Nikkor Z 85mm f/1.8 S
- Nikkor Z DX 12-28mm f/3.5-5.6 PZ VR
- Nikkor Z DX 16-50mm f/3.5-6.3 VR
- Nikkor Z DX 18-140mm f/3.5-6.3 VR
- Nikkor Z DX 24mm f/1.7
- Nikkor Z DX 50-250mm f/4.5-6.3 VR
- Nikkor Z MC 105mm f/2.8 VR S
- Nikkor Z MC 50mm f/2.8
- Nikon AF DC-Nikkor 105mm f/2D
- Nikon AF DC-Nikkor 135mm f/2D
- Nikon AF DX Fisheye-Nikkor 10.5mm f/2.8G ED
- Nikon AF Micro-Nikkor 105mm f/2.8D
- Nikon AF Micro-Nikkor 60mm f/2.8D
- Nikon AF Nikkor 105mm f/2.8D
- Nikon AF Nikkor 14mm f/2.8D ED
- Nikon AF Nikkor 180mm f/2.8D IF-ED
- Nikon AF Nikkor 20mm f/2.8D
- Nikon AF Nikkor 24mm f/2.8D
- Nikon AF Nikkor 24mm f/2.8D 54
- Nikon AF Nikkor 28mm f/1.4D
- Nikon AF Nikkor 28mm f/2.8D 62
- Nikon AF Nikkor 300mm f/4 IF-ED
- Nikon AF Nikkor 35-70mm f/2.8
- Nikon AF Nikkor 35mm f/2.8 PC "black knob"
- Nikon AF Nikkor 35mm f/2D 66
- Nikon AF Nikkor 50mm f/1.4D
- Nikon AF Nikkor 50mm f/1.8D
- Nikon AF Nikkor 70-210mm f/4-5.6
- Nikon AF Nikkor 85mm f/1.8 21
- Nikon AF Nikkor 85mm f/1.8D
- Nikon AF Zoom-Nikkor 18-35mm f/3.5-4.5D IF-ED
- Nikon AF Zoom-Nikkor 20-35mm f/2.8D IF
- Nikon AF Zoom-Nikkor 24-50mm f/3.3-4.5
- Nikon AF Zoom-Nikkor 24-50mm f/3.3-4.5D
- Nikon AF Zoom-Nikkor 24-85mm f/2.8-4D IF
- Nikon AF Zoom-Nikkor 28-105mm f/3.5-4.5D IF
- Nikon AF Zoom-Nikkor 28-200mm f/3.5-5.6D IF
- Nikon AF Zoom-Nikkor 28-200mm f/3.5-5.6G IF-ED
- Nikon AF Zoom-Nikkor 28-70mm f/3.5-4.5D
- Nikon AF Zoom-Nikkor 28-80mm f/3.3-5.6G
- Nikon AF Zoom-Nikkor 28-85mm f/3.5-4.5
- Nikon AF Zoom-Nikkor 35-70mm f/2.8D
- Nikon AF Zoom-Nikkor 35-70mm f/3.3-4.5 N
- Nikon AF Zoom-Nikkor 70-180mm f/4.5-5.6D ED Micro
- Nikon AF Zoom-Nikkor 70-210mm f/4
- Nikon AF Zoom-Nikkor 70-300mm f/4-5.6D ED
- Nikon AF Zoom-Nikkor 70-300mm f/4-5.6G
- Nikon AF Zoom-Nikkor 80-200mm f/2.8 ED
- Nikon AF Zoom-Nikkor 80-200mm f/2.8D ED
- Nikon AF Zoom-Nikkor 80-400mm f/4.5-5.6D ED VR 183
- Nikon AF-P DX Nikkor 10-20mm f/4.5-5.6G VR
- Nikon AF-P DX Nikkor 18-55mm f/3.5-5.6G VR
- Nikon AF-P DX Nikkor 70-300mm f/4.5-6.3G ED VR
- Nikon AF-P Nikkor 70-300mm f/4.5-5.6E ED VR
- Nikon AF-S DX Micro Nikkor 40mm f/2.8G
- Nikon AF-S DX Nikkor 10-24mm f/3.5-4.5G ED
- Nikon AF-S DX Nikkor 16-80mm f/2.8-4E ED VR
- Nikon AF-S DX Nikkor 18-140mm f/3.5-5.6G ED VR
- Nikon AF-S DX Nikkor 18-300mm f/3.5-5.6G ED VR
- Nikon AF-S DX Nikkor 18-300mm f/3.5-6.3G ED VR
- Nikon AF-S DX Nikkor 35mm f/1.8G
- Nikon AF-S DX Nikkor 55-200mm f/4-5.6G ED VR II
- Nikon AF-S DX Nikkor 55-300mm f/4.5-5.6G ED VR
- Nikon AF-S DX VR Nikkor 18-55mm f/3.5-5.6G II
- Nikon AF-S DX VR Zoom-Nikkor 18-200mm f/3.5-5.6G IF-ED
- Nikon AF-S DX VR Zoom-Nikkor 18-200mm f/3.5-5.6G IF-ED II
- Nikon AF-S DX Zoom-Nikkor 12-24mm f/4G IF-ED
- Nikon AF-S DX Zoom-Nikkor 16-85mm f/3.5-5.6G ED VR
- Nikon AF-S DX Zoom-Nikkor 17-55mm f/2.8G IF-ED
- Nikon AF-S DX Zoom-Nikkor 18-105mm f/3.5-5.6G ED VR
- Nikon AF-S DX Zoom-Nikkor 18-135mm f/3.5-5.6G IF-ED
- Nikon AF-S DX Zoom-Nikkor 18-55mm f/3.5-5.6G ED
- Nikon AF-S DX Zoom-Nikkor 18-55mm f/3.5-5.6G VR
- Nikon AF-S DX Zoom-Nikkor 18-70mm f/3.5-4.5G IF-ED
- Nikon AF-S DX Zoom-Nikkor 55-200mm f/4-5.6G ED
- Nikon AF-S Micro Nikkor 60mm f/2.8G ED
- Nikon AF-S Nikkor 105mm f/1.4E ED
- Nikon AF-S Nikkor 16-35mm f/4G ED VR
- Nikon AF-S Nikkor 18-35mm f/3.5-4.5G ED
- Nikon AF-S Nikkor 200-500mm f/5.6E ED VR
- Nikon AF-S Nikkor 20mm f/1.8G ED
- Nikon AF-S Nikkor 24-120mm f/4G ED VR 170
- Nikon AF-S Nikkor 24-70mm f/2.8E ED VR 170
- Nikon AF-S Nikkor 24-85 mm f/3.5-4.5G ED VR
- Nikon AF-S Nikkor 24mm f/1.8G ED
- Nikon AF-S Nikkor 28mm f/1.8G
- Nikon AF-S Nikkor 300mm f/4D IF-ED
- Nikon AF-S Nikkor 300mm f/4E PF ED VR
- Nikon AF-S Nikkor 35mm f/1.4G
- Nikon AF-S Nikkor 35mm f/1.8G ED
- Nikon AF-S Nikkor 500mm f/5.6E PF ED VR
- Nikon AF-S Nikkor 50mm f/1.4G
- Nikon AF-S Nikkor 50mm f/1.4G 160
- Nikon AF-S Nikkor 50mm f/1.8G
- Nikon AF-S Nikkor 50mm f/1.8G 176
- Nikon AF-S Nikkor 58mm f/1.4G
- Nikon AF-S Nikkor 600mm f/4E FL ED VR
- Nikon AF-S Nikkor 600mm f/4E FL ED VR + converter TC-14EIII
- Nikon AF-S Nikkor 600mm f/4G ED VR
- Nikon AF-S Nikkor 70-200mm f/2.8E FL ED VR 164
- Nikon AF-S Nikkor 70-200mm f/2.8G ED VR II + 2x extender
- Nikon AF-S Nikkor 70-200mm f/2.8G ED VR II 162
- Nikon AF-S Nikkor 80-400mm f/4.5-5.6G ED VR
- Nikon AF-S Nikkor 800mm f/5.6E FL ED VR
- Nikon AF-S Nikkor 85mm f/1.4G
- Nikon AF-S Nikkor 85mm f/1.8G 179
- Nikon AF-S VR Micro-Nikkor 105mm f/2.8G IF-ED 138
- Nikon AF-S VR Nikkor 400mm f/2.8G ED
- Nikon AF-S VR Nikkor 400mm f/2.8G ED + converter TC-14EIII
- Nikon AF-S VR Nikkor 400mm f/2.8G ED + converter TC-20EIII
- Nikon AF-S VR Nikkor 500mm f/4G ED
- Nikon AF-S VR Zoom-Nikkor 200-400mm f/4G IF-ED
- Nikon AF-S VR Zoom-Nikkor 24-120mm f/3.5-5.6G IF-ED
- Nikon AF-S VR Zoom-Nikkor 70-200mm f/2.8G IF-ED
- Nikon AF-S VR Zoom-Nikkor 70-200mm f/4G IF-ED
- Nikon AF-S VR Zoom-Nikkor 70-300mm f/4.5-5.6G IF-ED
- Nikon AF-S Zoom-Nikkor 14-24mm f/2.8G ED 146
- Nikon AF-S Zoom-Nikkor 17-35mm f/2.8D IF-ED
- Nikon AF-S Zoom-Nikkor 24-70mm f/2.8G ED
- Nikon AF-S Zoom-Nikkor 24-85mm f/3.5-4.5G IF-ED
- Nikon AF-S Zoom-Nikkor 28-300mm f/3.5-5.6G ED VR
- Nikon AI 80-200mm f/4.5 Zoom New
- Nikon AI Nikkor 15mm f/3.5
- Nikon AI Nikkor 45mm f/2.8 GN
- Nikon AI Nikkor 55mm f/1.2
- Nikon AI-S Fisheye-Nikkor 6mm f/2.8
- Nikon AI-S Nikkor 105mm f/2.5
- Nikon AI-S Nikkor 135mm f/2
- Nikon AI-S Nikkor 135mm f/2.8
- Nikon AI-S Nikkor 135mm f/3.5
- Nikon AI-S Nikkor 180mm f/2.8 ED
- Nikon AI-S Nikkor 200mm f/4
- Nikon AI-S Nikkor 20mm f/2.8
- Nikon AI-S Nikkor 24mm f/2
- Nikon AI-S Nikkor 24mm f/2.8
- Nikon AI-S Nikkor 28mm f/2
- Nikon AI-S Nikkor 28mm f/2.8
- Nikon AI-S Nikkor 28mm f/3.5 PC (unshifted)
- Nikon AI-S Nikkor 300mm f/4.5
- Nikon AI-S Nikkor 35mm f/1.4
- Nikon AI-S Nikkor 35mm f/2
- Nikon AI-S Nikkor 400mm f/3.5
- Nikon AI-S Nikkor 400mm f/3.5 + TC14B teleconverter
- Nikon AI-S Nikkor 500mm f/8 Reflex
- Nikon AI-S Nikkor 50mm f/1.2
- Nikon AI-S Nikkor 50mm f/1.4
- Nikon AI-S Nikkor 50mm f/1.8
- Nikon AI-S Nikkor 55mm f/2.8 Micro
- Nikon AI-S Nikkor 58mm f/1.2 Noct
- Nikon AI-S Zoom-Nikkor 50-135mm f/3.5
- Nikon AI-S Zoom-Nikkor 70-210mm f/4.5-5.6
- Nikon Lens Series E 100mm f/2.8
- Nikon Lens Series E 28mm f/2.8
- Nikon Lens Series E 50mm f/1.8
- Nikon Nikkor 50mm f/2
- Nikon Nikkor AI 20mm f/3.5
- Nikon Zoom-NIKKOR Auto 43-86mm F3.5

</details>

<details><summary><b>Nikon Corporation</b> (1)</summary>

- 1 Nikkor VR 10-30mm f/3.5-5.6 PD-ZOOM

</details>

<details><summary><b>NIKON CORPORATION</b> (2)</summary>

- Coolpix P1000
- Coolpix P1000 & compatibles

</details>

<details><summary><b>Olympus</b> (79)</summary>

- C-50Z & compatibles (Standard)
- C2040Z & compatibles (Standard)
- C2040Z & compatibles, with WCON-08B
- C4000Z & compatibles (Standard)
- C4000Z & compatibles, with A-28 iS/L converter
- C5060WZ & compatibles (Standard)
- C7000Z & compatibles (Standard)
- C700UZ & compatibles (Standard)
- C750UZ & compatibles (Standard)
- C8080WZ & compatibles (Standard)
- C860L & compatibles (Standard)
- M.40-150mm F2.8 + MC-14
- OLYMPUS M.12-200mm F3.5-6.3
- OLYMPUS M.12-45mm F4.0
- OLYMPUS M.30mm F3.5 Macro
- OLYMPUS M.40-150mm F2.8
- OLYMPUS M.8-25mm F4.0
- OLYMPUS OM 12-45mm F4.0
- OM 17mm F1.8 II
- Olympus 9mm Body Cap Lens Fisheye
- Olympus E-10 & compatibles (Standard)
- Olympus E-10 & compatibles, macro (full tele): 12 inches lens-to-subject
- Olympus E-10 & compatibles, macro (full tele): 16 inches lens-to-subject
- Olympus E-10 & compatibles, macro (full tele): 20 inches lens-to-subject
- Olympus E-10 & compatibles, macro (full tele): 8 inches lens-to-subject
- Olympus E-10 & compatibles, with B-300 (full tele)
- Olympus E-10 & compatibles, with WCON-08B
- Olympus M.Zuiko Digital 14-42mm f/3.5-5.6 II
- Olympus M.Zuiko Digital 17mm f/1.8
- Olympus M.Zuiko Digital 17mm f/2.8 Pancake
- Olympus M.Zuiko Digital 25mm f/1.8
- Olympus M.Zuiko Digital 45mm f/1.8
- Olympus M.Zuiko Digital ED 12-100mm f/4.0 IS Pro
- Olympus M.Zuiko Digital ED 12-40mm f/2.8 Pro
- Olympus M.Zuiko Digital ED 12-50mm f/3.5-6.3 EZ
- Olympus M.Zuiko Digital ED 12mm f/2.0
- Olympus M.Zuiko Digital ED 14-150mm f/4.0-5.6
- Olympus M.Zuiko Digital ED 14-150mm f/4.0-5.6 II
- Olympus M.Zuiko Digital ED 14-42mm f/3.5-5.6
- Olympus M.Zuiko Digital ED 14-42mm f/3.5-5.6 EZ
- Olympus M.Zuiko Digital ED 14-42mm f/3.5-5.6 II R
- Olympus M.Zuiko Digital ED 14-42mm f/3.5-5.6 L
- Olympus M.Zuiko Digital ED 17mm f/1.2 Pro
- Olympus M.Zuiko Digital ED 25mm f/1.2 Pro
- Olympus M.Zuiko Digital ED 40-150mm f/4.0-5.6 R
- Olympus M.Zuiko Digital ED 45mm f/1.2 Pro
- Olympus M.Zuiko Digital ED 60mm f/2.8 Macro
- Olympus M.Zuiko Digital ED 7-14mm f/2.8 Pro
- Olympus M.Zuiko Digital ED 75-300mm f/4.8-6.7 II
- Olympus M.Zuiko Digital ED 75mm f/1.8
- Olympus M.Zuiko Digital ED 8mm f/1.8 Fisheye Pro
- Olympus M.Zuiko Digital ED 9-18mm f/4.0-5.6
- Olympus OM-System Zuiko Auto-S 50 mm f/1.8 (Vers. S/N 5хххххх)
- Olympus Tough TG-4 & compatibles
- Olympus Tough TG-5
- Olympus Zuiko Digital 11-22mm f/2.8-3.5
- Olympus Zuiko Digital 14-45mm f/3.5-5.6
- Olympus Zuiko Digital 14-54mm f/2.8-3.5
- Olympus Zuiko Digital 25mm f/2.8
- Olympus Zuiko Digital 35mm f/3.5 Macro
- Olympus Zuiko Digital 40-150mm f/3.5-4.5
- Olympus Zuiko Digital 70-300mm F4.0-5.6
- Olympus Zuiko Digital ED 12-60mm f/2.8-4.0 SWD
- Olympus Zuiko Digital ED 14-35mm F2.0 SWD
- Olympus Zuiko Digital ED 14-42mm f/3.5-5.6
- Olympus Zuiko Digital ED 40-150mm f/4.0-5.6
- Olympus Zuiko Digital ED 50-200mm f/2.8-3.5
- Olympus Zuiko Digital ED 50-200mm f/2.8-3.5 SWD
- Olympus Zuiko Digital ED 50-200mm f/2.8-3.5 SWD + EC-14 1.4x extender
- Olympus Zuiko Digital ED 50-200mm f/2.8-3.5 SWD + EC-20 2x extender
- Olympus Zuiko Digital ED 50mm f/2.0 Macro
- Olympus Zuiko Digital ED 7-14mm f/4.0
- Olympus Zuiko Digital ED 9-18mm f/4.0-5.6
- Olympus Zuiko Digital Pro ED 35-100mm F2.0
- Stylus 1 & compatibles
- Stylus Epic & compatibles (Standard)
- Stylus V & compatibles (Standard)
- XZ-1 & compatibles (Standard)
- Zuiko Auto-S 50mm f/1.8

</details>

<details><summary><b>OM Digital Solutions</b> (4)</summary>

- OM 12-100mm F4.0
- OM 12-45mm F4.0
- OM 20mm F1.4
- OM 40-150mm F4.0

</details>

<details><summary><b>Opteka</b> (1)</summary>

- Opteka 15mm f/4 Wide Macro 1:1

</details>

<details><summary><b>Panasonic</b> (60)</summary>

- DC-ZS200 & compatibles
- DMC-FZ45 & compatibles (Standard)
- DMC-LF1 & compatibles
- LEICA DG 10-25/F1.7
- LEICA DG 100-400/F4.0-6.3
- LEICA DG 12-60/F2.8-4.0
- LEICA DG 50-200/F2.8-4.0
- LEICA DG 8-18/F2.8-4.0
- LEICA DG NOCTICRON 42.5/F1.2
- LEICA DG SUMMILUX 9/F1.7
- LUMIX G VARIO 100-300/F4.0-5.6II
- LUMIX G VARIO 35-100/F2.8
- LUMIX G VARIO 35-100/F2.8II
- LUMIX S 100mm F2.8 MACRO
- LUMIX S 18-40/F4.5-6.3
- LUMIX S 24-105/F4
- LUMIX S 24-60/F2.8
- LUMIX S 28-200/F4-7.1
- LUMIX S 50/F1.8
- Leica D Summilux 25mm F1.4 Asph.
- Leica D Vario-Elmar 14-150mm f/3.5-5.6 Asph. OIS
- Leica DG 12-60mm f/2.8-4.0
- Leica DG Macro-Elmarit 45mm f/2.8
- Leica DG Summilux 15mm f/1.7 Asph.
- Leica DG Summilux 25mm f/1.4 Asph.
- Leica DG Summilux 25mm f/1.4 II
- Lumix G 14mm f/2.5 Asph.
- Lumix G 14mm f/2.5 Asph. + GWC1 0.79x
- Lumix G 14mm f/2.5 II
- Lumix G 20mm f/1.7 Asph.
- Lumix G 20mm f/1.7 II Asph.
- Lumix G 25mm f/1.7 Asph.
- Lumix G 42.5mm f/1.7
- Lumix G Macro 30mm f/2.8
- Lumix G Vario 100-300mm f/4.0-5.6 Mega OIS
- Lumix G Vario 12-32mm f/3.5-5.6 Asph. Mega OIS
- Lumix G Vario 12-60mm f/3.5-5.6 Asph. Power OIS
- Lumix G Vario 14-140mm f/3.5-5.6
- Lumix G Vario 14-140mm f/3.5-5.6 II
- Lumix G Vario 14-42mm f/3.5-5.6 II Asph. Mega OIS
- Lumix G Vario 14-45mm f/3.5-5.6 Asph. Mega OIS
- Lumix G Vario 35-100mm f/4.0-5.6 Asph. Mega OIS
- Lumix G Vario 45-150mm f/4.0-5.6 Asph. Mega OIS
- Lumix G Vario 45-200mm f/4.0-5.6 II
- Lumix G Vario 45-200mm f/4.0-5.6 Mega OIS
- Lumix G Vario 7-14mm f/4.0 Asph.
- Lumix G Vario HD 14-140mm f/4.0-5.8 Asph. Mega OIS
- Lumix G X Vario 12-35mm f/2.8 Asph. Power OIS
- Lumix G X Vario 12-35mm f/2.8 II Asph. Power OIS
- Lumix G X Vario PZ 14-42mm f/3.5-5.6 Asph. Power OIS
- Lumix G X Vario PZ 14-42mm f/3.5-5.6 Asph. Power OIS + GWC1 0.79x
- Lumix G X Vario PZ 45-175mm f/4.0-5.6 Asph. Power OIS
- Lumix S 14-28/F4-5.6
- Lumix S 16-35/F4
- Lumix S 20-60/F3.5-5.6
- Lumix S 24/F1.8
- Lumix S 35/F1.8
- Lumix S 50/F1.4
- Lumix S 70-300/F4.5-5.6
- Lumix S 85/F1.8

</details>

<details><summary><b>Pentacon</b> (3)</summary>

- Pentacon 50mm f/1.8
- Pentacon 50mm f/1.8 auto multi coating
- Pentacon electric 2.8/29mm

</details>

<details><summary><b>Pentax</b> (81)</summary>

- 01 Standard Prime 8.5mm f/1.9 AL [IF]
- HD Pentax DA* 16-50mm f/2.8 ED PLM AW
- HD Pentax-D FA 15-30mm f/2.8 ED SDM WR
- HD Pentax-D FA 150-450mm f/4.5-5.6 ED DC AW
- HD Pentax-D FA 24-70mm f/2.8 ED SDM WR
- HD Pentax-D FA 28-105mm f/3.5-5.6 ED DC WR
- HD Pentax-D FA* 70-200mm f/2.8 ED DC AW
- HD Pentax-DA 16-85mm f/3.5-5.6 ED DC WR
- HD Pentax-DA 18-50mm f/4-5.6 DC WR RE
- HD Pentax-DA 20-40mm f/2.8-4 ED Limited DC WR
- HD Pentax-DA 21mm f/3.2 ED AL Limited
- HD Pentax-DA 55-300mm f/4-5.8 ED WR
- HD Pentax-DA 55-300mm f/4.5-6.3 ED PLM WR RE
- HD Pentax-DA 70mm f/2.4 Limited
- HD Pentax-DA* 11-18mm f/2.8 ED DC AW
- Pentax Optio 230GS & compatibles (Standard)
- Pentax Optio 430 & compatibles (Standard)
- Pentax Optio 43WR & compatibles (Standard)
- Pentax Optio 750Z & compatibles (Standard)
- Pentax SMC Takumar 50mm f/1.4
- Pentax-F 28-80mm f/3.5-4.5
- Super-Multi-Coated Takumar 400mm f/5.6
- Super-Takumar 50mm f/1.4
- Super-Takumar 55mm f/1.8
- Takumar 135mm f/2.5 Bayonet
- smc PENTAX DA* 60-250mm F4 [IF] SDM
- smc PENTAX-DA 14mm F2.8 ED[IF]
- smc Pentax DA* 60-250mm f/4 [IF] SDM
- smc Pentax K 30mm f/2.8
- smc Pentax Takumar 135mm f/2.5 (V2/43812)
- smc Pentax-A 28mm 1:2.8
- smc Pentax-A 50mm f/1.4
- smc Pentax-A 50mm f/1.7
- smc Pentax-D FA Macro 100mm f/2.8 WR
- smc Pentax-DA 12-24mm f/4 ED AL IF
- smc Pentax-DA 15mm f/4 ED AL Limited
- smc Pentax-DA 16-45mm f/4 ED AL
- smc Pentax-DA 17-70mm f/4 AL [IF] SDM
- smc Pentax-DA 18-135mm f/3.5-5.6 ED AL IF DC WR
- smc Pentax-DA 18-250mm f/3.5-6.3 ED AL [IF]
- smc Pentax-DA 18-55mm f/3.5-5.6 AL
- smc Pentax-DA 18-55mm f/3.5-5.6 AL II L WR
- smc Pentax-DA 21mm f/3.2 AL Limited
- smc Pentax-DA 35mm f/2.4 AL
- smc Pentax-DA 35mm f/2.8 Macro Limited
- smc Pentax-DA 40mm f/2.8 Limited
- smc Pentax-DA 40mm f/2.8 XS
- smc Pentax-DA 50-200mm f/4-5.6 DA ED
- smc Pentax-DA 50mm f/1.8
- smc Pentax-DA 55-300mm f/4-5.8 ED
- smc Pentax-DA 70mm f/2.4 Limited
- smc Pentax-DA Fish-Eye 10-17mm f/3.5-4.5 ED IF
- smc Pentax-DA L 18-50mm f/4-5.6 DC WR RE
- smc Pentax-DA L 50-200mm f/4-5.6 ED WR
- smc Pentax-DA L 55-300mm f/4-5.8 ED
- smc Pentax-DA* 16-50mm f/2.8 ED AL IF SDM
- smc Pentax-DA* 50-135mm f/2.8 ED IF SDM
- smc Pentax-DA* 55mm f/1.4 SDM
- smc Pentax-F 28mm f/2.8
- smc Pentax-F 35-80mm f/4-5.6
- smc Pentax-F ZOOM 35-70mm f/3.5-4.5
- smc Pentax-FA 28-70mm f/4 AL
- smc Pentax-FA 28mm f/2.8 AL
- smc Pentax-FA 31mm f/1.8 AL Limited
- smc Pentax-FA 43mm f/1.9 Limited
- smc Pentax-FA 50mm f/1.4
- smc Pentax-FA 50mm f/1.7
- smc Pentax-FA 77mm f/1.8 Limited
- smc Pentax-M 135mm f/3.5
- smc Pentax-M 150mm f/3.5
- smc Pentax-M 200mm f/4
- smc Pentax-M 28mm 1:3.5
- smc Pentax-M 28mm f/2.8
- smc Pentax-M 35mm 1:2
- smc Pentax-M 50mm f/1.4
- smc Pentax-M 50mm f/1.7
- smc Pentax-M 50mm f/2
- smc Pentax-M 75-150mm f/4
- smc Pentax-M 80-200mm f/4.5
- smc Pentax-M Macro 1:4 100mm
- smc Pentax-M Macro 1:4 50mm

</details>

<details><summary><b>Pergear</b> (2)</summary>

- Pergear 60mm f/2.8 MK2 Macro
- Pergear 7.5mm f/2.8

</details>

<details><summary><b>Petri</b> (2)</summary>

- Auto Petri 1:2.8 f=28mm
- Auto Petri 55mm f/1.8

</details>

<details><summary><b>Quantaray</b> (1)</summary>

- Quantaray M-AF 35-80mm F4-5.6

</details>

<details><summary><b>Ricoh</b> (9)</summary>

- Caplio GX & compatibles (Standard)
- Caplio GX & compatibles, with DW-4
- Caplio RR30 & compatibles (Standard)
- GR Digital & compatibles (Standard)
- Ricoh 50mm 1:2.0
- Ricoh GR & compatibles
- Ricoh GR IV & compatibles
- Ricoh XR Rikenon 1:1.4 50mm
- Rikenon P 50mm f/2

</details>

<details><summary><b>Ricoh Imaging Company</b> (1)</summary>

- HD PENTAX-D FA 21mm F2.4 ED Limited DC WR

</details>

<details><summary><b>Ricoh imaging company, ltd.</b> (4)</summary>

- Ricoh GR III & compatibles
- Ricoh GR III & compatibles + GW-4
- Ricoh GR III & compatibles, with GW-4
- Ricoh GR IIIx & compatibles

</details>

<details><summary><b>Rollei</b> (2)</summary>

- Rollei Rolleinar MC f/2.8 28mm
- Rollei Rolleinar MC f/4 21mm

</details>

<details><summary><b>Samsung</b> (19)</summary>

- EX2F & compatibles (Standard)
- NX-M 9-27mm F3.5-5.6
- NX-M 9mm F3.53.5-5.6
- SM-G950F
- Samsung Galaxy S21 ultrawide
- Samsung NX 10mm f/3.5 Fisheye
- Samsung NX 16-50mm f/2-2.8 S
- Samsung NX 16-50mm f/3.5-5.6 PZ ED OIS
- Samsung NX 16mm f/2.4 Pancake
- Samsung NX 18-55mm f/3.5-5.6 OIS
- Samsung NX 20-50mm f/3.5-5.6 ED
- Samsung NX 20mm f/2.8 Pancake
- Samsung NX 30mm f/2 Pancake
- Samsung NX 45mm f/1.8 2D/3D
- Samsung NX 50-150mm F2.8 S
- Samsung NX 50-200mm f/4-5.6
- Samsung S7 wide angle lens cover
- Samsung S8 wide angle lens
- WB2000 & compatibles (Standard)

</details>

<details><summary><b>Samyang</b> (30)</summary>

- Samyang 10mm f/2.8 ED AS NCS CS
- Samyang 12mm f/2.0 NCS CS
- Samyang 12mm f/2.8 Fish-Eye ED AS NCS
- Samyang 12mm f/3.1 VDSLR ED AS NCS Fish-eye
- Samyang 135mm f/2 ED UMC
- Samyang 14mm f/2.8 AE ED AS IF UMC
- Samyang 16mm f/2.0 ED AS UMC CS
- Samyang 20mm f/1.8 ED AS UMC
- Samyang 24mm f/1.4 ED AS IF UMC
- Samyang 35mm T1.5 Cine Lens
- Samyang 35mm f/1.4 AS UMC
- Samyang 500mm f/6.3 MC IF Mirror Lens
- Samyang 50mm f/1.4 AS UMC
- Samyang 7.5mm f/3.5 UMC Fish-eye MFT
- Samyang 800mm f/8 Mirror DX
- Samyang 85mm f/1.4 IF UMC Aspherical
- Samyang 8mm f/2.8 UMC Fish-eye
- Samyang 8mm f/3.5 Fish-Eye CS
- Samyang AF 12mm f/2.0
- Samyang AF 14mm f/2.8
- Samyang AF 18mm f/2.8
- Samyang AF 24mm f/1.8
- Samyang AF 24mm f/2.8
- Samyang AF 35mm f/1.8
- Samyang AF 35mm f/2.8
- Samyang AF 45mm f/1.8
- Samyang AF 75mm f/1.8
- Samyang AF 85mm f/1.4
- Samyang T-S 24mm f/3.5 ED AS UMC
- Samyang XP 10mm f/3.5

</details>

<details><summary><b>Schneider</b> (9)</summary>

- D-Xenon 1:3.5-5.6 18-55mm AL
- D-Xenon 1:4-5.6 50-200mm AL
- Schneider 28mm Digitar f/2.8
- Schneider 28mm f/2.8 PC
- Schneider 80mm Xenotar f/2.8
- Schneider LS 110mm f/2.8
- Schneider LS 55mm f/2.8
- Schneider LS 80mm f/2.8
- Schneider Retina-Curtagon 1:4/28mm

</details>

<details><summary><b>Schneider-Kreuznach</b> (9)</summary>

- Curtagon 35mm f/4
- PC-Super-Angulon 28mm f/2.8
- Retina-Curtagon 1:4/28mm
- Retina-Curtagon 28mm f/4
- Retina-Curtagon 35mm f/2.8
- Retina-Tele-Arton 85mm f/4
- Retina-Tele-Xenar 135mm f/4
- Retina-Xenon 50mm f/1.9
- Tele-Xenar 90mm f/3.5

</details>

<details><summary><b>Sigma</b> (121)</summary>

- 100-400mm F5-6.3 DG OS HSM | Contemporary 017
- 100-400mm F5-6.3 DG OS HSM | Contemporary 017 extender +1.4x
- 105mm F1.4 DG HSM | Art 018
- 105mm F2.8 DG DN MACRO | Art 020
- 14-24mm F2.8 DG DN | Art 019
- 16-28mm F2.8 DG DN | Contemporary 022
- 16-300mm F3.5-6.7 DC OS | Contemporary 025
- 17mm F4 DG DN | Contemporary 023
- 18-50mm F2.8 DC DN | Contemporary 021
- 20mm F2 DG DN | Contemporary 022
- 24-70mm F2.8 DG DN II | Art 024
- 24-70mm F2.8 DG DN | Art 019
- 24mm F2 DG DN | Contemporary 021
- 24mm F3.5 DG DN | Contemporary 021
- 28-70mm F2.8 DG DN | Contemporary 021
- 35mm F1.2 DG DN | Art 019
- 35mm F2 DG DN | Contemporary 020
- 40mm F1.4 DG HSM | Art 018
- 45mm F2.8 DG DN | Contemporary 019
- 60-600mm f/4.5-6.3 DG OS HSM | Sports 018 +1.4x extender
- 65mm F2 DG DN | Contemporary 020
- 70-200mm F2.8 DG OS HSM | Sports 018
- 85mm F1.4 DG DN | Art 020
- 90mm F2.8 DG DN | Contemporary 021
- E 30mm f/2.8
- SIGMA 30mm f/1.4 DC DN | Contemporary C 016
- Sigma 10-18mm F2.8 DC DN | Contemporary 023
- Sigma 10-20mm f/3.5 EX DC HSM
- Sigma 10-20mm f/4-5.6 EX DC HSM
- Sigma 100-300mm f/4 APO EX DG HSM
- Sigma 100-300mm f/4 APO EX DG HSM + Kenko Teleplus PRO 300 AF 1.4x DGX extender
- Sigma 105mm f/2.8 EX DG OS HSM Macro
- Sigma 10mm f/2.8 EX DC Fisheye HSM
- Sigma 12-24mm F4 DG HSM Art
- Sigma 12-24mm F4 DG HSM | A
- Sigma 12-24mm F4.5-5.6 II DG HSM
- Sigma 12-24mm f/4.5-5.6 EX DG Aspherical HSM
- Sigma 12-24mm f/4.5-5.6 EX DG HSM
- Sigma 14mm f/1.8 DG HSM | A
- Sigma 14mm f/2.8 EX
- Sigma 14mm f/2.8 EX Aspherical HSM
- Sigma 14mm f/3.5 EX
- Sigma 15-30mm f/3.5-4.5 EX DG Aspherical
- Sigma 150-500mm f/5-6.3 APO DG OS HSM
- Sigma 150-600mm f/5-6.3 DG OS HSM | C
- Sigma 150mm f/2.8 EX DG APO HSM Macro
- Sigma 150mm f/2.8 EX DG APO HSM Macro extender + 1.4x TC
- Sigma 15mm f/2.8 EX DG Diagonal Fisheye
- Sigma 16mm f/1.4 DC DN | Contemporary 017
- Sigma 16mm f/1.4 DC DN | Contemporary C 017
- Sigma 17-35mm F2.8-4 EX DG  Aspherical HSM
- Sigma 17-35mm f/2.8-4 EX DG
- Sigma 17-40mm F1.8 DC | Art 025
- Sigma 17-50mm f/2.8 EX DC HSM
- Sigma 17-50mm f/2.8 EX DC OS HSM
- Sigma 17-70mm f/2.8-4 DC MACRO OS HSM Contemporary
- Sigma 17-70mm f/2.8-4 DC Macro OS HSM
- Sigma 17-70mm f/2.8-4.5 DC Macro
- Sigma 18-125mm f/3.5-5.6 DC
- Sigma 18-200mm f/3.5-6.3 DC
- Sigma 18-200mm f/3.5-6.3 DC Macro OS HSM
- Sigma 18-200mm f/3.5-6.3 II DC OS HSM
- Sigma 18-250mm f/3.5-6.3 DC OS Macro HSM
- Sigma 18-300mm f/3.5-6.3 DC Macro OS HSM
- Sigma 18-35mm f/1.8 DC HSM [A]
- Sigma 18-50mm F2.8 DC DN | Contemporary 021
- Sigma 18-50mm f/2.8 EX DC
- Sigma 18-50mm f/3.5-5.6 DC
- Sigma 180mm f/2.8 EX DG OS HSM APO Macro
- Sigma 180mm f/5.6 APO Macro
- Sigma 19mm f/2.8 DN
- Sigma 19mm f/2.8 EX DN
- Sigma 20mm f/1.4 DG HSM | Art 015
- Sigma 20mm f/1.8 EX DG
- Sigma 24-105mm f/4.0 DG OS HSM [A]
- Sigma 24-35mm f/2 DG HSM | Art 015
- Sigma 24-60mm f/2.8 EX DG
- Sigma 24-70mm F2.8 DG OS HSM | Art 017
- Sigma 24-70mm f/2.8 EX DG Macro
- Sigma 24-70mm f/2.8 IF EX DG HSM
- Sigma 24mm F1.4 DG HSM | A
- Sigma 24mm f/1.4 DG HSM | [A] Art 015
- Sigma 24mm f/2.8 Super Wide II
- Sigma 28-300mm f/3.5-6.3 Macro ASP IF
- Sigma 28-70mm f/2.8 AF
- Sigma 28-70mm f/2.8 EX DG
- Sigma 28mm F1.4 DG HSM | A
- Sigma 28mm f/1.8 EX DG
- Sigma 30mm F1.4 DC HSM | Art 013
- Sigma 30mm f/1.4 EX DC HSM
- Sigma 30mm f/2.8 EX DN
- Sigma 35mm f/1.4 DG HSM
- Sigma 35mm f/1.4 DG HSM | A
- Sigma 35–80mm f/4–5.6 DL-II AF
- Sigma 4.5mm f/2.8 EX DC HSM circular fisheye
- Sigma 50-100mm f/1.8 DC HSM Art
- Sigma 50-150mm f/2.8 APO EX DC HSM II
- Sigma 50-150mm f/2.8 APO EX DC OS HSM
- Sigma 50-500mm f/4-6.3 EX DG HSM
- Sigma 50-500mm f/4.5-6.3 APO DG OS HSM
- Sigma 50mm f/1.4 DG HSM [A]
- Sigma 50mm f/1.4 EX DG HSM
- Sigma 55-200mm f/4-5.6 DC
- Sigma 56mm F1.4 DC DN | C 018
- Sigma 56mm F1.4 DC DN | Contemporary 018
- Sigma 60-600mm f/4.5-6.3 DG OS HSM | S
- Sigma 60mm f/2.8 DN
- Sigma 70-200mm f/2.8 APO EX HSM
- Sigma 70-200mm f/2.8 EX DG APO OS HSM
- Sigma 70-200mm f/2.8 EX DG Macro HSM II
- Sigma 70-300mm f/4-5.6 APO Macro Super II
- Sigma 70-300mm f/4-5.6 DG Macro
- Sigma 70-300mm f/4-5.6 DG OS
- Sigma 70-300mm f/4-5.6 DL Macro
- Sigma 70mm f/2.8 EX DG Macro
- Sigma 8-16mm f/4.5-5.6 DC HSM
- Sigma 80-400mm f/4.5-5.6 EX DG OS
- Sigma 85mm f/1.4 EX DG HSM
- Sigma 8mm f/3.5 EX DG Circular
- Sigma 90mm f/2.8 Macro AF
- Sigma DP2 & compatibles (Standard)

</details>

<details><summary><b>SLR Magic</b> (1)</summary>

- SLR Magic 8mm f/4

</details>

<details><summary><b>Soligor</b> (1)</summary>

- MC Soligor C/D Wide-Auto 1:2.8 f=24mm

</details>

<details><summary><b>Sony</b> (93)</summary>

- Carl Zeiss Distagon T* 24mm F2 ZA SSM (SAL24F20Z)
- Carl Zeiss Sonnar T* 135mm F1.8 ZA (SAL135F18Z)
- Carl Zeiss Vario-Sonnar T* 16-35mm f/2.8 ZA SSM II (SAL1635Z2)
- Carl Zeiss Vario-Sonnar T* 24-70mm f/2.8 ZA SSM II (SAL2470Z2)
- DSC-HX300 & compatibles (Standard)
- DSC-RX0M2
- DSC-RX100 & compatibles (Standard)
- DSC-RX100 II & compatibles
- DSC-RX100 III & compatibles
- DSC-RX100 VI & compatibles
- E 10-18mm f/4 OSS
- E 11mm f/1.8
- E 15mm F1.4 G
- E 16-55mm f/2.8 G
- E 16-70mm f/4 ZA OSS
- E 16mm f/2.8
- E 18-135mm f/3.5-5.6 OSS
- E 18-200mm f/3.5-6.3 OSS
- E 18-200mm f/3.5-6.3 OSS LE
- E 18-55mm f/3.5-5.6 OSS
- E 20mm f/2.8
- E 24mm f/1.8 ZA
- E 30mm f/3.5 Macro
- E 35mm f/1.8 OSS
- E 50mm f/1.8 OSS
- E 55-210mm f/4.5-6.3 OSS
- E 70-350mm f/4.5-6.3 G OSS
- E PZ 16-50mm f/3.5-5.6 OSS
- E PZ 18-105mm f/4 G OSS
- FE 100-400mm f/4.5-5.6 GM OSS
- FE 12-24mm f/4 G
- FE 14mm f/1.8 GM
- FE 16-35mm f/2.8 GM
- FE 16-35mm f/4 ZA OSS
- FE 16mm f/1.8 G
- FE 20-70mm f/4 G
- FE 200-600mm f/5.6-6.3 G OSS
- FE 20mm f/1.8 G
- FE 21mm f/2.8
- FE 24-105mm f/4 G OSS
- FE 24-240mm f/3.5-6.3 OSS
- FE 24-70mm f/2.8 GM
- FE 24-70mm f/2.8 GM II
- FE 24-70mm f/4 ZA OSS
- FE 24mm f/1.4 GM
- FE 24mm f/2.8 G
- FE 28-60mm f/4-5.6
- FE 28-70mm f/3.5-5.6 OSS
- FE 28mm f/2
- FE 35mm f/1.4 GM (SEL35F14GM)
- FE 35mm f/1.4 ZA
- FE 35mm f/1.8
- FE 35mm f/2.8 ZA
- FE 40mm f/2.5 G
- FE 50mm f/1.2 GM
- FE 50mm f/1.4 GM
- FE 50mm f/1.8
- FE 50mm f/2.5 G
- FE 50mm f/2.8 Macro
- FE 55mm f/1.8 ZA
- FE 70-200mm f/2.8 GM OSS
- FE 70-200mm f/4 G OSS
- FE 70-300mm f/4.5-5.6 G OSS
- FE 85mm f/1.4 GM
- FE 85mm f/1.8
- FE 90mm f/2.8 Macro G OSS
- Minolta/Sony AF 100mm F2.8 Macro (D)
- Minolta/Sony AF 500mm F8 Reflex
- Minolta/Sony AF DT 18-70mm f/3.5-5.6 (D)
- Sony 28-75mm F2.8 SAM (SAL2875)
- Sony 35mm F1.4 G (SAL35F14G)
- Sony 50mm f/1.4 (SAL50F14)
- Sony 70-300mm f/4.5-5.6 G SSM II (SAL70300G2)
- Sony 85mm F2.8 SAM (SAL85F28)
- Sony AF DT 16-105mm f/3.5-5.6
- Sony AF DT 18-250mm f/3.5-6.3 (SAL18250)
- Sony AF DT 30mm f/2.8 SAM Macro (SAL30M28)
- Sony AF DT 55-200mm f/4-5.6 SAM (SAL55200-2)
- Sony Carl Zeiss Planar T* 50mm F1.4 ZA SSM (SALF0F14Z)
- Sony DT 16-50mm f/2.8 SSM (SAL1650)
- Sony DT 18-135mm f/3.5-5.6 SAM
- Sony DT 18-135mm f/3.5-5.6 SAM (SAL18135)
- Sony DT 18-55mm f/3.5-5.6 SAM (SAL1855)
- Sony DT 35mm f/1.8 SAM (SAL35F18)
- Sony DT 50mm f/1.8 SAM
- Sony DT 55-300mm f/4.5-5.6 SAM (SAL55300)
- Sony RX10 & compatibles
- Sony RX10II & compatibles
- Sony RX10III & compatibles
- Sony Xperia Z3 & compatibles
- VCL-ECF1 fisheye converter
- VCL-ECU1 ultra wide converter
- ZV-1 & compatibles

</details>

<details><summary><b>Steinheil München</b> (3)</summary>

- Auto-D-Quinaron 35mm f/2.8
- Auto-D-Tele-Quinar 135mm f/2.8
- Culminar 85mm f/2.8

</details>

<details><summary><b>Sun</b> (2)</summary>

- Sun Wide YS-28 28mm f/2.8
- Sun Wide Zoom Macro 24-40mm f/3.5

</details>

<details><summary><b>Tamron</b> (82)</summary>

- 14-150mm F/3.5-5.8 DiIII C001:
- 18-300mm F3.5-6.3 DiIII-A VC VXD B061X
- AF 18-200mm f/3.5-6.3 Di II VC
- E 11-20mm F2.8 B060
- E 150-500mm F5-6.7 A057
- E 17-28mm F2.8-2.8
- E 17-70mm F2.8 B070
- E 18-300mm F3.5-6.3 B061
- E 20mm F2.8 F050
- E 24mm F2.8
- E 28-200mm F2.8-5.6 A071
- E 28-75mm F2.8-2.8
- E 35mm F2.8 F053
- E 50-400mm F4.5-6.3 A067
- E 70-180mm F2.8 A056
- E 70-180mm F2.8 A065
- E 70-300mm F4.5-6.3 A047
- TAMRON SP 90mm F/2.8 Di VC USD MACRO1:1 F004
- Tamron 10-24mm F/3.5-4.5 Di II VC HLD B023
- Tamron 10-24mm f/3.5-4.5 Di II VC HLD B023
- Tamron 100-400mm F/4.5-6.3 Di VC USD A035
- Tamron 16-300mm f/3.5-6.3 Di II VC PZD B016 Macro
- Tamron 16-300mm f/3.5-6.3 Di II VC PZD Macro B016
- Tamron 17-35mm f/2.8-4 Di OSD (A037)
- Tamron 18-200mm f/3.5-6.3 Di II VC
- Tamron 18-200mm f/3.5-6.3 Di III VC
- Tamron 18-400mm f/3.5-6.3 Di II VC HLD (B028)
- Tamron 200mm f/3.5 CT-200 BBAR
- Tamron 28-300mm f/3.5-6.3 Di VC PZD A010
- Tamron 35-100mm F2.8 A078 Z / E
- Tamron 35-150mm f/2.8-4.0 Di VC OSD A043
- Tamron 35-70mm f/3.5 CF Macro
- Tamron AF 16-300mm f/3.5-6.3 Di II VC PZD B016 Macro
- Tamron AF 17-50mm f/2.8 XR Di-II LD (Model A16)
- Tamron AF 18-200mm f/3.5-6.3 XR Di II LD Aspherical (IF) Macro
- Tamron AF 18-200mm f/3.5-6.3 XR Di II LD Aspherical (IF) Macro A14
- Tamron AF 18-250mm f/3.5-6.3 Di II LD Aspherical (IF) Macro
- Tamron AF 18-270mm F/3.5-6.3 Di II VC LD Aspherical (IF) Macro
- Tamron AF 18-270mm F/3.5-6.3 Di II VC PZD
- Tamron AF 18-270mm f/3.5-6.3 Di II VC PZD
- Tamron AF 18-400mm f/3.5-6.3 Di II VC HLD (B028)
- Tamron AF 19-35mm f/3.5-4.5
- Tamron AF 28-300mm f/3.5-6.3 XR Di LD Aspherical (IF)
- Tamron AF 28-300mm f/3.5-6.3 XR Di VC LD Aspherical (IF) Macro (A20)
- Tamron AF 70-300mm F4-5.6 LD Macro 1:2
- Tamron AF 70-300mm f/4-5.6 LD Macro 1:2
- Tamron AF 80-210mm f/4.5-5.6 280D
- Tamron E 11-20mm F2.8 DiIII-A RXD B060X RF
- Tamron E 150-500mm F/5-6.7 Di III VC VXD A057Z
- Tamron E 28-75mm F/2.8 Di III VXD G2 A063Z
- Tamron E 35-150mm f/2-2.8 2.0-2.8 Di III VXD A058Z
- Tamron SP 15-30mm f/2.8 Di VC USD (A012)
- Tamron SP 15-30mm f/2.8 Di VC USD G2 (A041)
- Tamron SP 24-70mm F/2.8 Di VC USD G2 (A032)
- Tamron SP 24-70mm f/2.8 Di VC USD
- Tamron SP 35mm f/1.4 Di USD
- Tamron SP 35mm f/1.8 Di VC USD F012
- Tamron SP 45mm F/1.8 Di VC USD
- Tamron SP 70-200mm F/2.8 Di VC USD G2
- Tamron SP 70-200mm f/2.8 Di VC USD A009
- Tamron SP 70-300mm f/4-5.6 Di USD
- Tamron SP 70-300mm f/4-5.6 Di VC USD (A005)
- Tamron SP 85mm f/1.8 Di VC USD F016
- Tamron SP 90mm F/2.8 Di VC USD MACRO 1:1 F004
- Tamron SP 90mm f/2.8 Di VC USD Macro 1:1
- Tamron SP 90mm f/2.8 Di VC USD Macro 1:1 F004
- Tamron SP AF 10-24mm f/3.5-4.5 Di II LD Aspherical (IF)
- Tamron SP AF 11-18mm f/4.5-5.6 Di-II LD Aspherical (IF)
- Tamron SP AF 150-600mm F/5-6.3 Di VC USD G2 (A022)
- Tamron SP AF 150-600mm f/5-6.3 Di VC USD (A011)
- Tamron SP AF 17-35mm f/2.8-4 Di LD Aspherical (IF)
- Tamron SP AF 17-50mm f/2.8 XR Di II LD Aspherical (IF)
- Tamron SP AF 17-50mm f/2.8 XR Di II VC LD Aspherical (IF)
- Tamron SP AF 24-135mm F/3.5-5.6 AD Aspherical (IF) Macro
- Tamron SP AF 24-135mm f/3.5-5.6 AD Aspherical (IF) Macro
- Tamron SP AF 28-105mm f/2.8 LD Aspherical IF
- Tamron SP AF 28-75mm f/2.8 XR Di LD Aspherical (IF)
- Tamron SP AF 28-75mm f/2.8 XR Di LD Aspherical (IF) Macro
- Tamron SP AF 60mm f/2 Di II LD (IF) Macro 1:1
- Tamron SP AF 70-200mm f/2.8 Di LD (IF) Macro (A001)
- Tamron SP AF 90mm F2.8 (172E)
- Tamron SP AF 90mm f/2.8 Di Macro 1:1

</details>

<details><summary><b>Tokina</b> (27)</summary>

- E 20mm f/2
- RMC Tokina 28mm f/2.8
- Tokina 11-16mm f/2.8 AT-X 116 AF Pro DX
- Tokina 12-24mm f/4 AT-X 124 AF Pro DX
- Tokina 17mm f/3.5 AT-X 17 AF Pro
- Tokina 17mm f/3.5 RMC II
- Tokina 19-35mm f/3.5-4.5 AF 193
- Tokina 20-35mm f/3.5-4.5 AF-235 II
- Tokina 28-70mm f/2.8 AT-X 287 Pro SV
- Tokina 500mm f/8 RMC Mirror Lens
- Tokina 80-200mm f/4.5-5.6 SZ-X
- Tokina AF 100mm f/2.8 AT-X Pro D M100 Macro
- Tokina AF 11-20mm f/2.8 AT-X Pro DX
- Tokina AF 12-28mm f/4 AT-X Pro DX
- Tokina AF 16-28mm f/2.8 AT-X Pro SD FX
- Tokina AF 28-80mm f/2.8 AT-X 280 Pro
- Tokina AF 80-200mm f/2.8 AT-X 828 Pro
- Tokina AT-X 11-20 F2.8 PRO DX Aspherical 11-20mm f/2.8
- Tokina AT-X 116 PRO DX II (AF 11-16mm f/2.8)
- Tokina AT-X 14-20 F2 PRO DX
- Tokina AT-X 24-70mm f/2.8 PRO FX
- Tokina AT-X AF SD 80-400mm f/4.5-5.6
- Tokina AT-X M100 100mm f/2.8 Pro D Macro AF
- Tokina AT-X M35 PRO DX (AF 35mm f/2.8 Macro)
- Tokina ATX-i 11-20mm F2.8 CF
- Tokina TELE-AUTO 135mm f/2.8
- Tokina atx-i 11-16mm F2.8 CF

</details>

<details><summary><b>Toshiba</b> (1)</summary>

- Tosner MC 28mm f/2.8

</details>

<details><summary><b>TTArtisan</b> (8)</summary>

- 50mm F1.4 Tilt
- AF 27mm f/2.8
- E 23mm F1.8
- TTARTISAN 40mmF2.0Z
- TTArtisan 35mm f/1.8
- TTArtisan 7.5mm f/2 Fisheye
- TTArtisan APS-C 23mm F1.4
- TTArtisan APS-C 25mm f/2.0

</details>

<details><summary><b>Venus</b> (11)</summary>

- Laowa 12mm f/2.8 Zero-D
- Laowa 15mm f/2.0 Zero-D
- Laowa 15mm f/4 Wide Angle Macro
- Laowa 17mm f/1.8 C-Dreamer
- Laowa 60mm f/2.8 2X Ultra-Macro
- Laowa 7.5mm f/2.0
- Laowa 85mm f/5.6 2X Ultra Macro APO
- Laowa 9mm f/2.8 Zero-D
- Laowa 9mm f/5.6 FF RL
- Laowa C&D-Dreamer MFT 10mm F2.0
- Laowa FF 10mm f/2.8 C&D Dreamer

</details>

<details><summary><b>Viltrox</b> (18)</summary>

- 23mmF1.4XM
- AF 13mm f/1.4 XF
- AF 27mm f/1.2 XF
- AF 56/1.4 XF
- Viltrox 15mm F1.7 E
- Viltrox 16mm F1.8 FE
- Viltrox 23mm F1.4 E
- Viltrox 25mm F1.7 E
- Viltrox 35mm F1.7 E
- Viltrox AF 20mm f/2.8 Z
- Viltrox AF 24mm f/1.8 Z
- Viltrox AF 33mm f/1.4 XF
- Viltrox AF 35mm f/1.8 Z
- Viltrox AF 50mm f/1.8 Z
- Viltrox AF 56mm f/1.2 E/Z
- Viltrox AF 85mm f/1.8 Z
- Viltrox AF 9mm f/2.8 Z/E
- Viltrox PFU RBMH 20mm f/1.8 ASPH

</details>

<details><summary><b>Vivitar</b> (2)</summary>

- Vivitar 100mm f/3.5 AF Macro
- Vivitar Series One 70-210mm 1:3.5 SN 22...

</details>

<details><summary><b>Voigtländer</b> (15)</summary>

- 35mm f/2
- APO-Lanthar 50mm F2 Aspherical
- Color-Skopar X 1:2.8/50
- Nokton 25mm f/0.95 II
- Skoparex 1:3.4/35
- Voigtlander APO-LANTHAR 50mm F2 Aspherical
- Voigtlander Color Skopar 20mm F3.5 SLII Aspherical
- Voigtlander Color-Ultron 50mm f/1.8
- Voigtlander Heliar-Hyper Wide 10mm F5.6
- Voigtlander Nokton 28mm F1.5 Aspherical
- Voigtlander Nokton 58mm F1.4 SLII
- Voigtlander Super Wide-Heliar 15mm f/4.5 III
- Voigtlander Ultron 40mm f/2 SLII Aspherical
- Voigtländer Color-Skopar X 1:2,8/50
- Voigtländer Skoparex 1:3,4/35

</details>

<details><summary><b>Yashica</b> (4)</summary>

- Yashica DSB 55mm f/2
- Yashica ML 50mm f/1.7
- Yashica ML 50mm f/2
- Yashica ML 55mm F/4 Macro

</details>

<details><summary><b>Yongnuo</b> (6)</summary>

- YN11mm F/1.8S DA DSM WL
- Yongnuo 25mm f/1.7 II
- Yongnuo YN 33mm F/1.4S DA DSM WL Pro
- Yongnuo YN 35mm f/2
- Yongnuo YN 50mm f/1.8
- Yongnuo YN 50mm f/1.8 II

</details>

<details><summary><b>Zeiss</b> (40)</summary>

- Carl Zeiss Distagon T* 2,8/21 ZE
- Carl Zeiss Distagon T* 2,8/21 ZF.2
- Carl Zeiss Distagon T* 2.8/21 ZF.2
- Carl Zeiss Distagon T* 2/35 ZF.2
- Carl Zeiss Distagon T* 3,5/18 ZF.2
- Carl Zeiss Distagon T* 3.5/18 ZF.2
- Carl Zeiss Jena 135mm f/3.5
- Carl Zeiss Jena 1Q Biotar 1:2 f=58mm T
- Carl Zeiss Jena Biotar 58mm f/2
- Carl Zeiss Jena Flektogon 20mm f/4
- Carl Zeiss Jena Flektogon 35mm f/2.4
- Carl Zeiss Jena Flektogon 4/20mm
- Carl Zeiss Jena Pancolar 50mm f/1.8
- Carl Zeiss Jena Sonnar 135mm f/3.5
- Carl Zeiss Jena Tessar 50mm f/2.8
- Carl Zeiss Jena Triotar-v2 135mm f/4
- Carl Zeiss Planar T* 1,4/50 ZF.2
- Carl Zeiss Planar T* 1.4/50 ZF.2
- Carl Zeiss Sonnar T* 135mm F1.8 ZA (SAL135F18Z)
- Carl Zeiss Sonnar T* 180mm f/2.8 MMJ
- Distagon 18mm f/4
- Distagon 28mm f/2.8 MMJ
- E 21mm F2.8
- E 25mm f/2
- E 50mm F2
- E 85mm F1.8
- Lumia 1020
- Lumia 950
- Planar 50mm f/1.7 AEJ
- Sonnar 85mm f/2.8 AEJ
- Standard
- Touit 1.8/32
- Touit 2.8/12
- Touit 2.8/50M
- ZEISS Batis 2/25
- Zeiss Batis 2.8/18
- Zeiss Distagon T* 25mm f/2.8 ZF.2
- Zeiss Makro-Planar T* 2/100 ZF.2
- Zeiss Milvus 1.4/50
- Zeiss Otus 85mm f/1.4

</details>

<details><summary><b>Zenit</b> (1)</summary>

- Zenitar MC 16mm f/2.8

</details>


## Camera bodies — lensfun

Bodies known to lensfun (used to pick the right lens profile): **1040 bodies** across **50 makers**. This is lensfun's body list, not a decode-support list — RAW decoding is LibRaw's job (above).

<details><summary><b>AEE DV</b> (1)</summary>

- AEE DV

</details>

<details><summary><b>Apple</b> (2)</summary>

- iPhone XS
- iPhone XS (tele)

</details>

<details><summary><b>Asahi Optical Co.,Ltd</b> (1)</summary>

- Pentax Optio 430

</details>

<details><summary><b>Canon</b> (256)</summary>

- 35mm film: full frame
- Canon DIGITAL IXUS 30
- Canon DIGITAL IXUS 40
- Canon DIGITAL IXUS 400
- Canon DIGITAL IXUS 430
- Canon DIGITAL IXUS 50
- Canon DIGITAL IXUS 500
- Canon DIGITAL IXUS 55
- Canon DIGITAL IXUS 70
- Canon DIGITAL IXUS 700
- Canon DIGITAL IXUS 750
- Canon DIGITAL IXUS 80 IS
- Canon DIGITAL IXUS 95 IS
- Canon DIGITAL IXUS II
- Canon DIGITAL IXUS i
- Canon DIGITAL IXUS v2
- Canon EOS 1000D
- Canon EOS 100D
- Canon EOS 10D
- Canon EOS 1100D
- Canon EOS 1200D
- Canon EOS 1300D
- Canon EOS 2000D
- Canon EOS 200D
- Canon EOS 200D II
- Canon EOS 20D
- Canon EOS 250D
- Canon EOS 300D DIGITAL
- Canon EOS 30D
- Canon EOS 350D DIGITAL
- Canon EOS 4000D
- Canon EOS 400D DIGITAL
- Canon EOS 40D
- Canon EOS 450D
- Canon EOS 500D
- Canon EOS 50D
- Canon EOS 550D
- Canon EOS 5D
- Canon EOS 5D Mark II
- Canon EOS 5D Mark III
- Canon EOS 5D Mark IV
- Canon EOS 5DS
- Canon EOS 5DS R
- Canon EOS 600D
- Canon EOS 60D
- Canon EOS 650D
- Canon EOS 6D
- Canon EOS 6D Mark II
- Canon EOS 700D
- Canon EOS 70D
- Canon EOS 750D
- Canon EOS 760D
- Canon EOS 77D
- Canon EOS 7D
- Canon EOS 7D Mark II
- Canon EOS 8000D
- Canon EOS 800D
- Canon EOS 80D
- Canon EOS 850D
- Canon EOS 9000D
- Canon EOS 90D
- Canon EOS D30
- Canon EOS D60
- Canon EOS DIGITAL REBEL
- Canon EOS DIGITAL REBEL XS
- Canon EOS DIGITAL REBEL XSi
- Canon EOS DIGITAL REBEL XT
- Canon EOS DIGITAL REBEL XTi
- Canon EOS Hi
- Canon EOS KISS M
- Canon EOS Kiss Digital
- Canon EOS Kiss Digital F
- Canon EOS Kiss Digital N
- Canon EOS Kiss Digital X
- Canon EOS Kiss Digital X2
- Canon EOS Kiss X10i
- Canon EOS Kiss X3
- Canon EOS Kiss X4
- Canon EOS Kiss X5
- Canon EOS Kiss X50
- Canon EOS Kiss X6i
- Canon EOS Kiss X7
- Canon EOS Kiss X70
- Canon EOS Kiss X7i
- Canon EOS Kiss X8i
- Canon EOS Kiss X9
- Canon EOS Kiss X90
- Canon EOS Kiss X9i
- Canon EOS M
- Canon EOS M10
- Canon EOS M100
- Canon EOS M2
- Canon EOS M200
- Canon EOS M3
- Canon EOS M5
- Canon EOS M50
- Canon EOS M50m2
- Canon EOS M6
- Canon EOS M6 Mark II
- Canon EOS R
- Canon EOS R1
- Canon EOS R10
- Canon EOS R100
- Canon EOS R3
- Canon EOS R5
- Canon EOS R5 C
- Canon EOS R50
- Canon EOS R50 V
- Canon EOS R5m2
- Canon EOS R6
- Canon EOS R6 Mark III
- Canon EOS R6 V
- Canon EOS R6m2
- Canon EOS R7
- Canon EOS R8
- Canon EOS REBEL SL1
- Canon EOS REBEL SL2
- Canon EOS REBEL SL3
- Canon EOS REBEL T1i
- Canon EOS REBEL T2i
- Canon EOS REBEL T3
- Canon EOS REBEL T3i
- Canon EOS REBEL T4i
- Canon EOS REBEL T5
- Canon EOS REBEL T5i
- Canon EOS REBEL T7i
- Canon EOS RP
- Canon EOS Rebel T100
- Canon EOS Rebel T6
- Canon EOS Rebel T6i
- Canon EOS Rebel T6s
- Canon EOS Rebel T7
- Canon EOS Rebel T8i
- Canon EOS-1D
- Canon EOS-1D Mark II
- Canon EOS-1D Mark II N
- Canon EOS-1D Mark III
- Canon EOS-1D Mark IV
- Canon EOS-1D X
- Canon EOS-1D X Mark II
- Canon EOS-1Ds
- Canon EOS-1Ds Mark II
- Canon EOS-1Ds Mark III
- Canon IXUS 125 HS
- Canon IXUS 220 HS
- Canon IXY 220F
- Canon IXY DIGITAL 200a
- Canon IXY DIGITAL 30
- Canon IXY DIGITAL 40
- Canon IXY DIGITAL 400
- Canon IXY DIGITAL 450
- Canon IXY DIGITAL 50
- Canon IXY DIGITAL 500
- Canon IXY DIGITAL 55
- Canon PowerShot A10
- Canon PowerShot A1200
- Canon PowerShot A20
- Canon PowerShot A30
- Canon PowerShot A40
- Canon PowerShot A4000 IS
- Canon PowerShot A490
- Canon PowerShot A495
- Canon PowerShot A510
- Canon PowerShot A520
- Canon PowerShot A60
- Canon PowerShot A610
- Canon PowerShot A620
- Canon PowerShot A640
- Canon PowerShot A650 IS
- Canon PowerShot A70
- Canon PowerShot A720 IS
- Canon PowerShot A75
- Canon PowerShot A80
- Canon PowerShot A85
- Canon PowerShot A95
- Canon PowerShot ELPH 110 HS
- Canon PowerShot G1
- Canon PowerShot G1 X
- Canon PowerShot G1 X Mark II
- Canon PowerShot G1 X Mark III
- Canon PowerShot G10
- Canon PowerShot G11
- Canon PowerShot G12
- Canon PowerShot G15
- Canon PowerShot G16
- Canon PowerShot G2
- Canon PowerShot G3
- Canon PowerShot G3 X
- Canon PowerShot G5
- Canon PowerShot G5 X
- Canon PowerShot G5 X 16:9
- Canon PowerShot G5 X 4:3
- Canon PowerShot G5 X Mark II
- Canon PowerShot G6
- Canon PowerShot G7
- Canon PowerShot G7 X
- Canon PowerShot G7 X 16:9
- Canon PowerShot G7 X 4:3
- Canon PowerShot G7 X Mark II
- Canon PowerShot G7 X Mark II 16:9
- Canon PowerShot G7 X Mark II 4:3
- Canon PowerShot G7 X Mark III
- Canon PowerShot G9
- Canon PowerShot G9 X
- Canon PowerShot G9 X Mark II
- Canon PowerShot Pro1
- Canon PowerShot Pro70
- Canon PowerShot Pro90 IS
- Canon PowerShot S1 IS
- Canon PowerShot S100
- Canon PowerShot S110
- Canon PowerShot S120
- Canon PowerShot S2 IS
- Canon PowerShot S200
- Canon PowerShot S30
- Canon PowerShot S40
- Canon PowerShot S400
- Canon PowerShot S410
- Canon PowerShot S45
- Canon PowerShot S5 IS
- Canon PowerShot S50
- Canon PowerShot S500
- Canon PowerShot S60
- Canon PowerShot S70
- Canon PowerShot S80
- Canon PowerShot S90
- Canon PowerShot S95
- Canon PowerShot SD10
- Canon PowerShot SD100
- Canon PowerShot SD110
- Canon PowerShot SD1100 IS
- Canon PowerShot SD200
- Canon PowerShot SD300
- Canon PowerShot SD400
- Canon PowerShot SD450
- Canon PowerShot SD500
- Canon PowerShot SD550
- Canon PowerShot SD950 IS
- Canon PowerShot SX1 IS
- Canon PowerShot SX10 IS
- Canon PowerShot SX130 IS
- Canon PowerShot SX150 IS
- Canon PowerShot SX160 IS
- Canon PowerShot SX220 HS
- Canon PowerShot SX230 HS
- Canon PowerShot SX240 HS
- Canon PowerShot SX260 HS
- Canon PowerShot SX30 IS
- Canon PowerShot SX50 HS
- Canon PowerShot SX510 HS
- Canon PowerShot SX60 HS
- Canon PowerShot SX700 HS
- Canon PowerShot SX710 HS
- EOS D2000
- IXY Digital 600
- IXY Digital 700

</details>

<details><summary><b>Casio</b> (3)</summary>

- QV-3000EX
- QV-3500EX
- QV-4000

</details>

<details><summary><b>Casio Computer Co.,Ltd</b> (8)</summary>

- EX-P600
- EX-P700
- EX-Z3
- EX-Z30
- EX-Z4
- EX-Z40
- EX-Z55
- EX-Z750

</details>

<details><summary><b>Casio Computer Co.,Ltd.</b> (1)</summary>

- EX-FH20

</details>

<details><summary><b>Contax</b> (1)</summary>

- 35mm film: full frame

</details>

<details><summary><b>DJI</b> (8)</summary>

- FC3411
- FC3582
- FC6310
- FC6310R
- Mavic Air FC2103
- Mavic Pro FC220
- Phantom 3 Pro FC300X
- Phantom Vision FC200

</details>

<details><summary><b>Eastman Kodak Company</b> (4)</summary>

- Kodak CX6330 Zoom Digital Camera
- Kodak CX7525 Zoom Digital Camera
- Kodak DC120 ZOOM Digital Camera
- Kodak Digital Science DC50 Zoom Camera

</details>

<details><summary><b>Fujifilm</b> (85)</summary>

- FinePix 3800
- FinePix A370
- FinePix E550
- FinePix F10
- FinePix F11
- FinePix F200EXR
- FinePix F601 ZOOM
- FinePix F710
- FinePix F770EXR
- FinePix F810
- FinePix HS20EXR
- FinePix HS30EXR
- FinePix S20Pro
- FinePix S3000
- FinePix S304
- FinePix S3Pro
- FinePix S5100
- FinePix S5500
- FinePix S5600
- FinePix S5Pro
- FinePix S602 ZOOM
- FinePix S7000
- FinePix S9000
- FinePix S9500
- FinePix S9600
- FinePix X100
- FinePix2800ZOOM
- FinePixS1Pro
- FinePixS2Pro
- GFX 100
- GFX 50R
- GFX 50S
- GFX100 II
- GFX100RF
- GFX100S
- GFX100S II
- GFX50S II
- IS Pro
- X-A1
- X-A10
- X-A2
- X-A3
- X-A5
- X-A7
- X-E1
- X-E2
- X-E2S
- X-E3
- X-E4
- X-E5
- X-H1
- X-H2
- X-H2S
- X-M1
- X-M5
- X-Pro1
- X-Pro2
- X-Pro3
- X-S1
- X-S10
- X-S20
- X-T1
- X-T10
- X-T100
- X-T2
- X-T20
- X-T200
- X-T3
- X-T30
- X-T30 II
- X-T30 III
- X-T4
- X-T5
- X-T50
- X10
- X100F
- X100S
- X100T
- X100V
- X100VI
- X20
- X30
- X70
- XF10
- XQ1

</details>

<details><summary><b>Generic</b> (8)</summary>

- Crop-factor 0.8 (Medium Format)
- Crop-factor 1.0 (Full Frame)
- Crop-factor 1.1
- Crop-factor 1.3 (APS-H)
- Crop-factor 1.5 (APS-C)
- Crop-factor 1.6 (APS-C)
- Crop-factor 1.7
- Crop-factor 2.0 (Four-Thirds)

</details>

<details><summary><b>GitUp</b> (1)</summary>

- Git2

</details>

<details><summary><b>GoPro</b> (7)</summary>

- HD2
- HERO4 Black
- HERO4 Silver
- HERO5 Black
- Hero10 black
- Hero11 Black
- Hero3+ black

</details>

<details><summary><b>Hasselblad</b> (9)</summary>

- CFV 100C/907X
- CFV II 50C/907X
- Hasselblad 500 mech.
- Hasselblad H3D
- L1D-20c
- L2D-20c
- X1D II 50C
- X2D 100C
- X2D II 100C

</details>

<details><summary><b>Honor</b> (1)</summary>

- DLI-L22

</details>

<details><summary><b>Huawei</b> (3)</summary>

- CLT-L29
- VOG-L29
- WAS-LX1A

</details>

<details><summary><b>KMZ</b> (6)</summary>

- Zenit 122
- Zenit 122K
- Zenit 212K
- Zenit 312K
- Zenit 412LS
- Zenit KM

</details>

<details><summary><b>Kodak</b> (5)</summary>

- DCS Pro 14N
- DCS Pro 14nx
- DCS Pro SLR/c
- DCS Pro SLR/n
- DCS520

</details>

<details><summary><b>Konica Minolta</b> (10)</summary>

- DiMAGE A200
- DiMAGE Z10
- DiMAGE Z20
- DiMAGE Z3
- DiMAGE Z5
- DiMAGE Z6
- Dynax 5D
- Dynax 7D
- Maxxum 5D
- Maxxum 7D

</details>

<details><summary><b>Konica Minolta Camera, Inc.</b> (4)</summary>

- DiMAGE A2
- DiMAGE G400
- DiMAGE Z2
- Revio KD-420Z

</details>

<details><summary><b>Leica</b> (6)</summary>

- D-Lux 3
- D-Lux 4
- D-Lux2
- Digilux 2
- Digilux 3
- Leica X Vario (Typ 107)

</details>

<details><summary><b>Leica Camera AG</b> (32)</summary>

- C-Lux
- Digilux 3
- Leica CL
- Leica CL (Typ 7323)
- Leica M (Typ 240)
- Leica M EV1
- Leica M Monochrom (Typ 246)
- Leica M10
- Leica M10 Monochrom
- Leica M10-D
- Leica M10-P
- Leica M10-R
- Leica M11
- Leica M11 Monochrom
- Leica M11-D
- Leica M11-P
- Leica Q (Typ 116)
- Leica Q2
- Leica Q2 Mono
- Leica Q3
- Leica Q3 Mono
- Leica SL (Typ 601)
- Leica SL2
- Leica SL2-S
- Leica SL3
- Leica SL3-S
- Leica T (Typ 701)
- Leica TL
- Leica TL2
- Leica X Vario (Typ 107)
- M8 Digital Camera
- M9 Digital Camera

</details>

<details><summary><b>LEICA CAMERA AG</b> (1)</summary>

- LEICA Q3 43

</details>

<details><summary><b>LG Mobile</b> (1)</summary>

- LG-H815

</details>

<details><summary><b>Mamiya</b> (2)</summary>

- Mamiya 645
- Mamiya ZD

</details>

<details><summary><b>Microsoft</b> (2)</summary>

- Lumia 950
- Lumia 950 XL

</details>

<details><summary><b>Minolta Co., Ltd.</b> (8)</summary>

- DiMAGE 7
- DiMAGE 7Hi
- DiMAGE 7i
- DiMAGE A1
- DiMAGE X
- DiMAGE Xi
- DiMAGE Xt
- DiMAGE Z1

</details>

<details><summary><b>Nikon</b> (24)</summary>

- 35mm film: full frame
- Coolpix P330
- Coolpix P340
- Coolpix P60
- Coolpix P6000
- Coolpix P7000
- Coolpix P7800
- Coolpix S3300
- E4200
- E4500
- E4800
- E5000
- E5200
- E5400
- E5700
- E5900
- E7600
- E7900
- E8400
- E8700
- E8800
- E950
- E990
- E995

</details>

<details><summary><b>Nikon Corporation</b> (82)</summary>

- Coolpix A
- Coolpix P1000
- Coolpix P1100
- Nikon 1 AW1
- Nikon 1 J1
- Nikon 1 J2
- Nikon 1 J3
- Nikon 1 J4
- Nikon 1 J5
- Nikon 1 S1
- Nikon 1 S2
- Nikon 1 V1
- Nikon 1 V2
- Nikon 1 V3
- Nikon D1
- Nikon D100
- Nikon D1H
- Nikon D1X
- Nikon D200
- Nikon D2H
- Nikon D2Hs
- Nikon D2X
- Nikon D2Xs
- Nikon D3
- Nikon D300
- Nikon D3000
- Nikon D300S
- Nikon D3100
- Nikon D3200
- Nikon D3300
- Nikon D3400
- Nikon D3500
- Nikon D3S
- Nikon D3X
- Nikon D4
- Nikon D40
- Nikon D40X
- Nikon D4s
- Nikon D5
- Nikon D50
- Nikon D500
- Nikon D5000
- Nikon D5100
- Nikon D5200
- Nikon D5300
- Nikon D5500
- Nikon D5600
- Nikon D6
- Nikon D60
- Nikon D600
- Nikon D610
- Nikon D70
- Nikon D700
- Nikon D7000
- Nikon D70s
- Nikon D7100
- Nikon D7200
- Nikon D750
- Nikon D7500
- Nikon D780
- Nikon D80
- Nikon D800
- Nikon D800E
- Nikon D810
- Nikon D850
- Nikon D90
- Nikon Df
- Nikon Z 30
- Nikon Z 5
- Nikon Z 50
- Nikon Z 6
- Nikon Z 6_2
- Nikon Z 7
- Nikon Z 7_2
- Nikon Z 8
- Nikon Z 9
- Nikon Z f
- Nikon Z fc
- Nikon Z50_2
- Nikon Z5_2
- Nikon Z6_3
- Nikon ZR

</details>

<details><summary><b>Nokia</b> (2)</summary>

- Lumia 1020
- Lumia 1520

</details>

<details><summary><b>Olympus</b> (2)</summary>

- Stylus Epic
- mju-II

</details>

<details><summary><b>Olympus Corporation</b> (17)</summary>

- C5060WZ
- C8080WZ
- E-1
- E-M10 Mark III
- E-M10MarkII
- E-M10MarkIIIS
- E-M10MarkIV
- E-M1MarkIII
- E-M5MarkIII
- E-PL8
- E-PL9
- TG-1
- TG-2
- TG-3
- TG-4
- TG-5
- TG-6

</details>

<details><summary><b>Olympus Imaging Corp.</b> (44)</summary>

- C7070WZ
- C70Z,C7000Z
- E-3
- E-30
- E-300
- E-330
- E-400
- E-410
- E-420
- E-450
- E-5
- E-500
- E-510
- E-520
- E-600
- E-620
- E-M1
- E-M10
- E-M1MarkII
- E-M5
- E-M5MarkII
- E-P1
- E-P2
- E-P3
- E-P5
- E-P7
- E-PL1
- E-PL1s
- E-PL2
- E-PL3
- E-PL5
- E-PL6
- E-PL7
- E-PM1
- E-PM2
- PEN-F
- SP350
- SP500UZ
- SP560UZ
- Stylus1
- Stylus1,1s
- XZ-1
- XZ-2
- u-miniD,Stylus V

</details>

<details><summary><b>Olympus Optical Co.,Ltd</b> (12)</summary>

- C2040Z
- C3040Z
- C4040Z
- C4100Z,C4000Z
- C5050Z
- C700UZ
- C730UZ
- C750UZ
- C860L,D360L
- E-10
- E-20,E-20N,E-20P
- X-2,C-50Z

</details>

<details><summary><b>OM Digital Solutions</b> (5)</summary>

- OM-1
- OM-1MarkII
- OM-3
- OM-5
- OM-5MarkII

</details>

<details><summary><b>Panasonic</b> (136)</summary>

- DC-FZ10002
- DC-G100
- DC-G100D
- DC-G110
- DC-G9
- DC-G90
- DC-G91
- DC-G95
- DC-G95D
- DC-G97
- DC-G99
- DC-G99D
- DC-G9M2
- DC-GH5
- DC-GH5M2
- DC-GH5S
- DC-GH6
- DC-GH7
- DC-GX7MK3
- DC-GX800
- DC-GX880
- DC-GX9
- DC-LX100M2
- DC-S1
- DC-S1H
- DC-S1M2
- DC-S1M2ES
- DC-S1R
- DC-S1RM2
- DC-S5
- DC-S5D
- DC-S5M2
- DC-S5M2X
- DC-S9
- DC-TZ200
- DC-TZ200D
- DC-TZ202
- DC-TZ220
- DC-TZ90
- DC-TZ91
- DC-TZ96
- DC-TZ99
- DC-ZS200
- DC-ZS200D
- DC-ZS220
- DC-ZS70
- DMC-FX150
- DMC-FX2
- DMC-FX7
- DMC-FX8
- DMC-FX9
- DMC-FZ10
- DMC-FZ100
- DMC-FZ100 (3:2)
- DMC-FZ1000
- DMC-FZ150
- DMC-FZ18
- DMC-FZ20
- DMC-FZ200
- DMC-FZ2000
- DMC-FZ2500
- DMC-FZ28
- DMC-FZ3
- DMC-FZ30
- DMC-FZ300
- DMC-FZ330
- DMC-FZ35
- DMC-FZ40
- DMC-FZ40 (3:2)
- DMC-FZ45
- DMC-FZ45 (3:2)
- DMC-FZ5
- DMC-FZ50
- DMC-FZ8
- DMC-G1
- DMC-G10
- DMC-G2
- DMC-G3
- DMC-G5
- DMC-G6
- DMC-G7
- DMC-G70
- DMC-G8
- DMC-G80
- DMC-G81
- DMC-G85
- DMC-GF1
- DMC-GF2
- DMC-GF3
- DMC-GF5
- DMC-GF6
- DMC-GF7
- DMC-GF8
- DMC-GH1
- DMC-GH2
- DMC-GH3
- DMC-GH4
- DMC-GM1
- DMC-GM5
- DMC-GX1
- DMC-GX7
- DMC-GX8
- DMC-GX80
- DMC-GX85
- DMC-L1
- DMC-L10
- DMC-LC1
- DMC-LF1
- DMC-LX1
- DMC-LX10
- DMC-LX100
- DMC-LX15
- DMC-LX2
- DMC-LX3
- DMC-LX5
- DMC-LX7
- DMC-LZ1
- DMC-LZ2
- DMC-TZ100
- DMC-TZ101
- DMC-TZ110
- DMC-TZ60
- DMC-TZ61
- DMC-TZ70
- DMC-TZ71
- DMC-TZ80
- DMC-TZ81
- DMC-TZ90
- DMC-TZ91
- DMC-TZ96
- DMC-ZS100
- DMC-ZS110
- DMC-ZS40
- DMC-ZS50
- DMC-ZS60
- DMC-ZS70

</details>

<details><summary><b>Pentax</b> (19)</summary>

- 35mm film: full frame
- Pentax 645D
- Pentax K-01
- Pentax K-30
- Pentax K-5
- Pentax K-5 II
- Pentax K-5 II s
- Pentax K-50
- Pentax K-500
- Pentax K-7
- Pentax K-m
- Pentax K-r
- Pentax K-x
- Pentax K2000
- Pentax K200D
- Pentax Q
- Pentax Q-S1
- Pentax Q10
- Pentax Q7

</details>

<details><summary><b>Pentax Corporation</b> (19)</summary>

- Pentax *ist D
- Pentax *ist DL
- Pentax *ist DL2
- Pentax *ist DS
- Pentax *ist DS2
- Pentax K100D
- Pentax K100D Super
- Pentax K10D
- Pentax K110D
- Pentax K20D
- Pentax Optio 230GS
- Pentax Optio 330GS
- Pentax Optio 33L
- Pentax Optio 33LF
- Pentax Optio 43WR
- Pentax Optio 450
- Pentax Optio 550
- Pentax Optio 555
- Pentax Optio 750Z

</details>

<details><summary><b>Phase One</b> (3)</summary>

- IQ140
- IQ180
- P 25

</details>

<details><summary><b>Ricoh</b> (4)</summary>

- Caplio GX
- Caplio GX8
- Caplio RR30
- GR Digital

</details>

<details><summary><b>Ricoh Imaging Company, Ltd.</b> (18)</summary>

- GR
- Pentax 645Z
- Pentax K-1
- Pentax K-1 Mark II
- Pentax K-3
- Pentax K-3 II
- Pentax K-3 Mark III
- Pentax K-3 Mark III Monochrome
- Pentax K-70
- Pentax K-S1
- Pentax K-S2
- Pentax KF
- Pentax KP
- Ricoh GR III
- Ricoh GR III HDF
- Ricoh GR IIIx
- Ricoh GR IIIx HDF
- Ricoh GR IV

</details>

<details><summary><b>Rolleiflex</b> (1)</summary>

- 2.8E

</details>

<details><summary><b>Samsung</b> (25)</summary>

- EK-GN120
- EX2F
- NX mini
- NX1
- NX10
- NX100
- NX1000
- NX11
- NX1100
- NX20
- NX200
- NX2000
- NX210
- NX30
- NX300
- NX3000
- NX300M
- NX5
- NX500
- SM-G935F
- SM-G9500
- SM-G950F
- SM-G991B
- SM-N950U
- WB2000

</details>

<details><summary><b>Samsung Techwin</b> (2)</summary>

- GX-1L
- GX-1S

</details>

<details><summary><b>Samsung Techwin Co.</b> (2)</summary>

- GX20
- SAMSUNG GX10

</details>

<details><summary><b>SEIKO EPSON CORP.</b> (1)</summary>

- R-D1

</details>

<details><summary><b>Sigma</b> (20)</summary>

- Sigma BF
- Sigma DP1
- Sigma DP1 Merrill
- Sigma DP1S
- Sigma DP1X
- Sigma DP2
- Sigma DP2 Merrill
- Sigma DP2S
- Sigma DP2X
- Sigma DP3 Merrill
- Sigma SD1
- Sigma SD1 Merrill
- Sigma SD10
- Sigma SD14
- Sigma SD15
- Sigma SD9
- Sigma fp
- Sigma fp L
- sd Quattro
- sd Quattro H

</details>

<details><summary><b>Sony</b> (125)</summary>

- Cybershot
- DSC-F828
- DSC-H1
- DSC-HX20V
- DSC-HX300
- DSC-P100
- DSC-P150
- DSC-P200
- DSC-P73
- DSC-P93
- DSC-R1
- DSC-RX0
- DSC-RX0M2
- DSC-RX10
- DSC-RX100
- DSC-RX100M2
- DSC-RX100M3
- DSC-RX100M4
- DSC-RX100M5
- DSC-RX100M5A
- DSC-RX100M6
- DSC-RX100M7
- DSC-RX100M7A
- DSC-RX10M2
- DSC-RX10M3
- DSC-RX10M4
- DSC-RX1R
- DSC-RX1RM2
- DSC-RX1RM3
- DSC-S60
- DSC-S80
- DSC-S90
- DSC-ST80
- DSC-T1
- DSC-V1
- DSC-V3
- DSC-W1
- DSC-W12
- DSC-W15
- DSC-W5
- DSC-W7
- DSLR-A100
- DSLR-A200
- DSLR-A230
- DSLR-A290
- DSLR-A300
- DSLR-A330
- DSLR-A350
- DSLR-A380
- DSLR-A390
- DSLR-A450
- DSLR-A500
- DSLR-A550
- DSLR-A560
- DSLR-A580
- DSLR-A700
- DSLR-A850
- DSLR-A900
- ILCA-68
- ILCA-77M2
- ILCA-99M2
- ILCE-1
- ILCE-1M2
- ILCE-3000
- ILCE-5000
- ILCE-5100
- ILCE-6000
- ILCE-6100
- ILCE-6100A
- ILCE-6300
- ILCE-6400
- ILCE-6400A
- ILCE-6500
- ILCE-6600
- ILCE-6700
- ILCE-7
- ILCE-7C
- ILCE-7CM2
- ILCE-7CR
- ILCE-7M2
- ILCE-7M3
- ILCE-7M4
- ILCE-7M5
- ILCE-7R
- ILCE-7RM2
- ILCE-7RM3
- ILCE-7RM3A
- ILCE-7RM4
- ILCE-7RM4A
- ILCE-7RM5
- ILCE-7RM6
- ILCE-7S
- ILCE-7SM2
- ILCE-7SM3
- ILCE-9
- ILCE-9M2
- ILCE-9M3
- ILME-FX2
- ILME-FX3
- ILME-FX30
- NEX-3
- NEX-3N
- NEX-5
- NEX-5N
- NEX-5R
- NEX-5T
- NEX-6
- NEX-7
- NEX-C3
- NEX-F3
- SLT-A33
- SLT-A35
- SLT-A37
- SLT-A55V
- SLT-A57
- SLT-A58
- SLT-A65V
- SLT-A77V
- SLT-A99
- SLT-A99V
- Xperia Z3
- ZV-1
- ZV-E1
- ZV-E10
- ZV-E10M2

</details>

<details><summary><b>YI Technology</b> (1)</summary>

- M1

</details>

