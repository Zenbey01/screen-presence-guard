# Screen Presence Guard

จอไม่ดับเมื่อคุณอยู่ — มืดเองเมื่อคุณไม่อยู่ และ **ไม่ต้องใส่รหัสตอนกลับมา**

> **Demo 0.5**

## หลักการ

ไม่ได้สั่งให้ OS ดับจอ (นั่นจะโดน lock screen) แต่เอา **หน้าต่างสีดำเต็มจอ**
มาคลุมไว้แทน — ตาเห็นเหมือนจอดับ แต่ OS ยังคิดว่าจอเปิดอยู่ ไม่มี lock ไม่ต้องใส่รหัส

- ตรวจใบหน้าจากเว็บแคมทุก N วินาที (ปรับได้)
- เจอหน้า → กันจอดับ (`SetThreadExecutionState` / `IOPMAssertion`)
- ไม่เจอหน้าครบ N วินาที → overlay ดำคลุมจอ
- กลับมา / ขยับเมาส์ / กดคีย์ → overlay หายทันที

## ดาวน์โหลด (ไม่ต้องมี Python)

โหลดจากหน้า [**Releases**](https://github.com/Zenbey01/screen-presence-guard/releases/latest)

| เครื่อง | ไฟล์ | วิธีเปิด |
|---|---|---|
| Windows 10/11 | `ScreenPresenceGuard.zip` | แตกไฟล์ → ดับเบิลคลิก `ScreenPresenceGuard.exe` |
| macOS (Apple Silicon) | `ScreenPresenceGuard-macOS.zip` | แตกไฟล์ → **คลิกขวา** ที่ `.app` → **Open** → **Open** |

บน Windows อยากได้ shortcut บน Desktop ให้รัน `ติดตั้ง shortcut.bat` ในโฟลเดอร์ที่แตกออกมา

ครั้งแรก Windows อาจขึ้น *"Windows protected your PC"* / macOS อาจบล็อกด้วย Gatekeeper
เพราะไฟล์ไม่ได้เซ็นใบรับรอง — Windows กด **More info** → **Run anyway**,
macOS ต้องคลิกขวา → Open (ดับเบิลคลิกเฉย ๆ จะไม่ผ่าน)

ทั้งสองไฟล์บิ้วโดย GitHub Actions จากสอร์สใน repo นี้ ตรวจขั้นตอนได้ที่แท็บ Actions

## รองรับ Windows และ macOS

ทุกการเรียก OS ถูกรวมไว้ที่แพ็กเกจเดียว `spgplatform/` แล้วเลือก backend
ตาม `sys.platform` — `main.py` ไม่เรียก `ctypes` เองอีกต่อไป

| หน้าที่ | Windows | macOS |
|---|---|---|
| กันจอดับ / กัน sleep | `SetThreadExecutionState` | `IOPMAssertionCreateWithName` |
| reset idle timer | `SendInput` | `CGEventPost` (ต้องขยับจริง) |
| ขยับ / อ่านตำแหน่งเมาส์ | `SetCursorPos`, `GetCursorPos` | `CGWarpMouseCursorPosition`, `CGEventGetLocation` |
| อ่านค่า idle | `GetLastInputInfo` | `CGEventSourceSecondsSinceLastEventType` |
| ตรวจคีย์บอร์ด | `GetAsyncKeyState` | `CGEventSourceKeyState` |
| ขอบจอหลายจอ | `GetSystemMetrics(76-79)` | `CGGetActiveDisplayList` + `CGDisplayBounds` |

macOS ใช้ `ctypes` เรียก CoreGraphics/IOKit ตรง ๆ **ไม่ต้องลง `pyobjc`**

### macOS ต้องเปิดสิทธิ์ก่อน

ไปที่ **System Settings → Privacy & Security** แล้วเปิดให้แอปนี้:

| สิทธิ์ | ถ้าไม่เปิด |
|---|---|
| **Camera** | ตรวจใบหน้าไม่ได้เลย (ระบบถามให้ตอนกด Start) |
| **Accessibility** | หมุนเมาส์กันล็อก + กันจอหลับไม่ทำงาน — **เงียบ ไม่มี error** |
| **Input Monitoring** | คีย์บอร์ดปลุกจอไม่ได้ (เมาส์ยังปลุกได้) |

สองอันหลังระบบไม่ถามให้ ต้องเปิดเอง — แอปจะเขียนเตือนลงแท็บ **บันทึก** ทุกครั้งที่กด Start
ถ้ายังไม่ได้เปิด

**ข้อจำกัดบน macOS**: overlay ดำครอบได้ทีละจอ ถ้าต่อจอนอกไว้ จอที่เหลือจะยังสว่าง
(บน Windows ครอบครบทุกจอ)

exe/app ของ PyInstaller ผูกกับ OS ที่บิ้ว — ต้องบิ้วแยกกันคนละไฟล์ ซึ่ง CI ทำให้แล้ว

## รันจากสอร์ส (สำหรับนักพัฒนา)

```bash
pip install -r requirements.txt
python main.py
```

## การใช้งาน

กด **Start** เพื่อเริ่มตรวจจับ ปิดหน้าต่างหรือกด **Minimize to tray** เพื่อย่อลง system tray

ตั้งค่าได้จากแท็บด้านขวา:

| แท็บ | ทำอะไร |
|---|---|
| **ตั้งค่า** | เวลารอก่อนดับจอ (5 วินาที – 15 นาที), ความถี่การตรวจ, เมาส์/คีย์บอร์ดปลุกจอ, mouse jiggle |
| **ใบหน้า** | ลงทะเบียนใบหน้า (จับ 40 เฟรม) และล้างข้อมูล |
| **บันทึก** | log การทำงาน |

### ลงทะเบียนใบหน้า (ไม่บังคับ)

ถ้าไม่ลงทะเบียน → **ใบหน้าใครก็นับว่ามีคนอยู่**
ถ้าลงทะเบียนแล้ว → นับเฉพาะหน้าคุณ (LBPH recognizer)

กด **ลงทะเบียนใบหน้าใหม่** ได้หลายรอบ ตัวอย่างจะถูก *เพิ่ม* เข้ากองเดิม
(หลายรอบ = หลายมุม/แสง = แม่นขึ้น) ระหว่างจับหน้ากดปุ่มเดิมซ้ำเพื่อยกเลิกได้

### Mouse jiggle

บางองค์กรบังคับ lock เครื่องเมื่อไม่มี input จริง ๆ ไม่ว่าจอจะเปิดหรือไม่
เปิด jiggle ให้เมาส์ขยับเป็นวงกลมเล็ก ๆ ตามรอบเวลาที่ตั้ง — มี 2 ตัวแยกกัน
คือตอน **จอเปิด** และตอน **จอมืด** เปิดเฉพาะที่ต้องการได้

สองตัวนี้ทำงานสลับกัน ไม่มีทางทำงานพร้อมกัน เพราะเช็คสถานะจอตรงข้ามกัน
ถ้าเปิดเฉพาะ "จอเปิด" แล้วจอมืดอยู่ มันจะข้ามไปเฉย ๆ (log ขึ้น `ข้าม — จอดำอยู่`)

**ดูยังไงว่าเมาส์ขยับจริง** — ตอนจอมืดมองไม่เห็นเคอร์เซอร์ เพราะ overlay
ซ่อนมันไว้ ให้ดูจากแท็บ **บันทึก** แทน:

```
[jiggle-OFF] วงกลมจาก (700,400) | idle ก่อน 34s
[jiggle-OFF] ✓ เสร็จ (700,400) | idle หลัง 0ms
```

`idle` คือค่าที่ policy ล็อกเครื่องแบบมาตรฐานใช้วัด เห็น `34s → 0ms` แปลว่า
OS รับ input จริง ไม่ใช่แค่โค้ดเดินผ่าน ตอนจอมืดจะมี heartbeat ทุกนาที
บอกค่า idle ล่าสุดด้วย ถ้าตัวเลขไต่ขึ้นเรื่อย ๆ คือกันล็อกไม่อยู่

บน macOS ถ้าตัวเลข idle **ไม่ลดลงเลย** แปลว่ายังไม่ได้เปิดสิทธิ์ **Accessibility** —
macOS จะทิ้ง event ที่แอปยิงเข้าไปแบบเงียบ ๆ ไม่มี error

**หมายเหตุ** — jiggle ไม่ยืดเวลาดับจอ ตั้งรอบหมุนเท่าไรก็ไม่กระทบเวลานับถอยหลัง

## ไฟล์ที่สร้างขึ้นตอนใช้งาน

`face_model.yml` + `face_imgs.pkl` — ข้อมูลใบหน้าของคุณ เป็นข้อมูลส่วนตัว
**อย่าแชร์ไปกับคนอื่น**

- รันจาก source: เก็บข้างๆ `main.py` (อยู่ใน `.gitignore` แล้ว)
- รันจาก exe/app ที่ build แล้ว: Windows เก็บที่ `%LOCALAPPDATA%\ScreenPresenceGuard`,
  macOS เก็บที่ `~/Library/Application Support/ScreenPresenceGuard`
  เพราะตัว bundle อาจถูกติดตั้งในที่ที่เขียนไฟล์ไม่ได้

ย้ายจาก source ไป exe หรือกลับกัน จะมองไม่เห็นข้อมูลเดิม ต้องลงทะเบียนใหม่ 1 ครั้ง

## แจกจ่าย (ไม่ต้องมี Python ที่เครื่องปลายทาง)

บิ้วบน OS ไหน ได้ไฟล์ของ OS นั้น — บิ้วข้าม OS ไม่ได้

```powershell
.\build.ps1     # Windows → dist\ScreenPresenceGuard\ + ScreenPresenceGuard.zip
```
```bash
./build.sh      # macOS   → dist/ScreenPresenceGuard.app + ScreenPresenceGuard-macOS.zip
```

ผู้รับบน Windows: แตกไฟล์ → รัน `ติดตั้ง shortcut.bat` → เปิดจาก shortcut บน Desktop
ผู้รับบน macOS: แตกไฟล์ → คลิกขวาที่ `.app` → Open → ลากไปไว้ใน Applications ได้เลย

ปกติไม่ต้องบิ้วเอง — แค่ push tag `demo-*` หรือ `v*` แล้ว GitHub Actions
บิ้วให้ทั้งสอง OS แล้วแนบเข้า Release เดียวกัน

## Detection stack

| ชั้น | ใช้ทำอะไร |
|---|---|
| MediaPipe `FaceDetector` (Tasks API) | **ตัดสินว่ามีคนอยู่หรือไม่** — ทนแว่น/หันหน้า |
| Haar cascade | วาดกรอบใน preview + fallback ถ้า MediaPipe โหลดไม่ได้ |
| LBPH (`cv2.face`) | แยกว่าเป็นหน้าคุณหรือคนอื่น |

MediaPipe ต้องมีไฟล์โมเดล `blaze_face_short_range.tflite` อยู่ข้าง `main.py`
(มีอยู่ใน repo แล้ว) ถ้าโหลดไม่ได้ โปรแกรมจะ **เขียนสาเหตุลง log** แล้วถอยไปใช้ Haar
ซึ่งแม่นน้อยกว่าอย่างเห็นได้ชัด
