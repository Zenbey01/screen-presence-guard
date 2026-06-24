# screen-presence-guard

จอไม่ดับเองเมื่อคุณอยู่ — ดับเองเมื่อคุณไม่อยู่

## วิธีการทำงาน

- ตรวจจับใบหน้าจากเว็บแคมทุก 2 วินาที (ใช้ OpenCV Haar Cascade)
- ถ้าเห็นใบหน้า: ล็อค Windows ไม่ให้ดับจอ
- ถ้าไม่เห็นใบหน้านาน 30 วินาที: ดับจอ (แต่โปรแกรมยังทำงานอยู่)
- เมื่อกลับมา: จอติดขึ้นเองทันที

## ติดตั้ง

```bash
pip install -r requirements.txt
```

## รัน

```bash
python main.py
```

กด `Ctrl+C` เพื่อหยุด

## ปรับค่า

แก้ในไฟล์ `main.py`:
- `ABSENCE_TIMEOUT = 30` — กี่วินาทีที่ไม่เจอใบหน้าแล้วดับจอ
- `CHECK_INTERVAL = 2` — ตรวจสอบทุกกี่วินาที

## หลักการ (Ponytail / YAGNI)

ไม่มี dependency พิเศษ — ใช้แค่ `opencv-python` (มี face detection ในตัว) และ `ctypes` (stdlib ของ Python) ควบคุม Windows API โดยตรง
