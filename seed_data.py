"""Generator data dummy inventory.

Pemakaian:
  python seed_data.py                         -> buat data/produk.csv & data/transaksi.csv
  python seed_data.py --upload SHEET_ID cred.json
                                              -> buat CSV + upload ke Google Sheet
"""
import random
import sys
from datetime import datetime, timedelta

import pandas as pd

random.seed(42)

# (Nama, Kategori, Supplier, Harga_Beli, Harga_Jual, Stok_Min)
PRODUK = [
    ("Mouse Wireless Logitech M185", "Elektronik", "PT Maju Digital", 110000, 149000, 20),
    ("Keyboard Mekanik Rexus", "Elektronik", "PT Maju Digital", 350000, 459000, 10),
    ("Headset Gaming Fantech", "Elektronik", "CV Sinar Tekno", 220000, 299000, 12),
    ("Flashdisk 64GB Sandisk", "Elektronik", "PT Maju Digital", 62000, 89000, 30),
    ("Power Bank 10000mAh", "Elektronik", "CV Sinar Tekno", 125000, 179000, 15),
    ("Kabel HDMI 2m", "Elektronik", "CV Sinar Tekno", 28000, 49000, 25),
    ("Webcam Full HD 1080p", "Elektronik", "PT Maju Digital", 190000, 269000, 8),
    ("Pulpen Gel 0.5mm (box 12)", "Alat Tulis", "UD Kertas Jaya", 24000, 36000, 40),
    ("Buku Tulis A5 (pak 10)", "Alat Tulis", "UD Kertas Jaya", 38000, 55000, 35),
    ("Kertas HVS A4 80gr (rim)", "Alat Tulis", "UD Kertas Jaya", 48000, 62000, 50),
    ("Stapler Kenko HD-10", "Alat Tulis", "UD Kertas Jaya", 15000, 24000, 20),
    ("Spidol Whiteboard (box 12)", "Alat Tulis", "UD Kertas Jaya", 55000, 78000, 15),
    ("Map Folder Plastik (pak 12)", "Alat Tulis", "UD Kertas Jaya", 27000, 40000, 25),
    ("Kursi Kantor Ergonomis", "Perabot", "PT Furnindo", 650000, 899000, 5),
    ("Meja Kerja 120cm", "Perabot", "PT Furnindo", 780000, 1090000, 4),
    ("Lemari Arsip 4 Laci", "Perabot", "PT Furnindo", 1150000, 1550000, 3),
    ("Rak Buku 5 Susun", "Perabot", "PT Furnindo", 420000, 599000, 5),
    ("Lampu Meja LED", "Perabot", "CV Sinar Tekno", 75000, 115000, 12),
    ("Kardus Packing 30x20x15 (pak 10)", "Kemasan", "PT Pack Prima", 32000, 48000, 40),
    ("Lakban Bening 48mm", "Kemasan", "PT Pack Prima", 8500, 14000, 60),
    ("Bubble Wrap 50m", "Kemasan", "PT Pack Prima", 85000, 120000, 15),
    ("Plastik Polymailer (pak 100)", "Kemasan", "PT Pack Prima", 42000, 65000, 30),
    ("Kopi Arabika 250gr", "Makanan & Minuman", "CV Nusantara Food", 48000, 69000, 25),
    ("Teh Celup (box 25)", "Makanan & Minuman", "CV Nusantara Food", 9500, 15000, 45),
    ("Air Mineral 600ml (dus 24)", "Makanan & Minuman", "CV Nusantara Food", 42000, 55000, 30),
    ("Gula Pasir 1kg", "Makanan & Minuman", "CV Nusantara Food", 15500, 18500, 40),
]
PREFIX = {"Elektronik": "ELK", "Alat Tulis": "ATK", "Perabot": "PRB",
          "Kemasan": "KMS", "Makanan & Minuman": "FNB"}
OPERATOR = ["Andi", "Budi", "Citra", "Dewi", "Eko"]
CATATAN_MASUK = ["Restock rutin", "PO bulanan", "Pengiriman supplier", "Retur pelanggan"]
CATATAN_KELUAR = ["Penjualan online", "Penjualan toko", "Pesanan korporat", "Kebutuhan internal"]


def buat_data():
    counter, produk = {}, []
    for nama, kat, sup, hb, hj, smin in PRODUK:
        counter[kat] = counter.get(kat, 0) + 1
        produk.append({
            "SKU": f"{PREFIX[kat]}-{counter[kat]:03d}", "Nama": nama, "Kategori": kat,
            "Supplier": sup, "Lokasi": f"Rak {random.choice('ABCD')}{random.randint(1, 5)}",
            "Harga_Beli": hb, "Harga_Jual": hj, "Stok_Min": smin,
            "Stok_Awal": random.randint(int(smin * 1.5), int(smin * 6)),
        })
    stok = {p["SKU"]: p["Stok_Awal"] for p in produk}
    # beberapa produk sengaja dibuat laris supaya muncul peringatan stok
    laris = set(random.sample(list(stok), 6))

    now = datetime.now()
    waktu = sorted(now - timedelta(minutes=random.randint(0, 45 * 24 * 60)) for _ in range(650))
    trx = []
    for w in waktu:
        sku = random.choice(list(laris)) if random.random() < 0.35 else random.choice(list(stok))
        r = random.random()
        smin = next(p["Stok_Min"] for p in produk if p["SKU"] == sku)
        if r < 0.66:
            tipe, qty = "Keluar", random.randint(1, max(2, int(smin * 0.6)))
            qty = min(qty, stok[sku])
            if qty <= 0:
                continue
            stok[sku] -= qty
            cat = random.choice(CATATAN_KELUAR)
        elif r < 0.95:
            tipe, qty = "Masuk", random.randint(int(smin * 0.5), int(smin * 3))
            stok[sku] += qty
            cat = random.choice(CATATAN_MASUK)
        else:
            tipe, qty = "Penyesuaian", random.choice([-3, -2, -1, 1, 2])
            if stok[sku] + qty < 0:
                continue
            stok[sku] += qty
            cat = "Stock opname"
        trx.append({"ID": f"T{len(trx) + 1:05d}", "Waktu": w.strftime("%Y-%m-%d %H:%M:%S"),
                    "SKU": sku, "Tipe": tipe, "Qty": qty,
                    "Operator": random.choice(OPERATOR), "Catatan": cat})
    return pd.DataFrame(produk), pd.DataFrame(trx)


def upload(sheet_id, cred_path, p, t):
    import gspread
    gc = gspread.service_account(filename=cred_path)
    sh = gc.open_by_key(sheet_id)
    for nama, df in (("Produk", p), ("Transaksi", t)):
        try:
            ws = sh.worksheet(nama)
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(nama, rows=len(df) + 100, cols=len(df.columns))
        ws.update([df.columns.tolist()] + df.astype(object).values.tolist(), value_input_option="USER_ENTERED")
        ws.freeze(rows=1)
        print(f"Upload sheet '{nama}': {len(df)} baris")


if __name__ == "__main__":
    p, t = buat_data()
    p.to_csv("produk.csv", index=False)
    t.to_csv("transaksi.csv", index=False)
    print(f"produk.csv ({len(p)} produk), transaksi.csv ({len(t)} transaksi)")
    if len(sys.argv) == 4 and sys.argv[1] == "--upload":
        upload(sys.argv[2], sys.argv[3], p, t)
