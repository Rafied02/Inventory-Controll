# Inventory Pro (Python + Streamlit + Google Sheets)

Fitur: dashboard realtime, tab Laporan & KPI (turnover, DOI, sell-through, margin, ABC, restock, dead stock; unduh Excel/HTML) (auto-refresh 15 detik), filter interaktif, catat transaksi masuk/keluar/penyesuaian
dengan validasi stok, tambah produk, peringatan restock, login password opsional.

## 1. Coba lokal (mode demo, tanpa Google Sheet)
    pip install -r requirements.txt
    python seed_data.py        # data dummy sudah ada di folder data/
    streamlit run app.py

## 2. Sambungkan ke Google Sheet
1. Buka https://console.cloud.google.com -> buat project -> aktifkan **Google Sheets API**.
2. IAM & Admin -> Service Accounts -> buat akun -> Keys -> Add key -> JSON (simpan sebagai credentials.json).
3. Buat Google Sheet kosong, klik **Share**, tambahkan `client_email` dari JSON sebagai **Editor**.
4. Salin ID sheet dari URL (`/spreadsheets/d/<ID>/edit`).
5. Upload data dummy:
       python seed_data.py --upload ID_SHEET credentials.json
   Akan terbentuk 2 tab: `Produk` dan `Transaksi`.

## 3. Publikasikan ke web (gratis)
1. Upload folder ini ke repo GitHub (credentials.json & secrets.toml jangan ikut; sudah ada di .gitignore).
2. Buka https://share.streamlit.io -> New app -> pilih repo, file utama `app.py`.
3. App settings -> Secrets -> tempel isi `.streamlit/secrets.toml.example` yang sudah diisi.
4. Deploy. Anda mendapat URL publik; hapus `app_password` hanya jika memang ingin terbuka untuk umum.

## Struktur sheet
Produk: SKU, Nama, Kategori, Supplier, Lokasi, Harga_Beli, Harga_Jual, Stok_Min, Stok_Awal
Transaksi: ID, Waktu, SKU, Tipe (Masuk/Keluar/Penyesuaian), Qty, Operator, Catatan
Stok saat ini = Stok_Awal + total Masuk - total Keluar + Penyesuaian (dihitung otomatis).

## Login, peran, dan log audit
- **Setup pertama**: saat aplikasi dibuka dan belum ada pengguna, muncul layar "Setup awal". Isi **kode setup** (= nilai `app_password` di Secrets), lalu buat akun admin pertama.
- **Pengguna** disimpan di tab `Pengguna` pada Google Sheet (password di-hash PBKDF2, tidak pernah disimpan polos).
- **Peran**: `admin` = semua menu (Laporan & KPI, Produk Baru, Pengguna, Log Audit, simpan tema, transaksi Penyesuaian).
  `staf` = Dashboard, Stok, dan Transaksi Masuk/Keluar. Nama operator pada transaksi terisi otomatis dari akun yang login.
- **Log audit** tersimpan di tab `Audit`: login/logout, transaksi, produk baru, perubahan pengguna, simpan tema (siapa, kapan, apa).
- Jangan edit tab `Pengguna` dan `Audit` manual di Google Sheet, kecuali Anda paham risikonya.
