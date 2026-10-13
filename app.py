"""Inventory Pro - sistem inventory realtime & interaktif (Streamlit + Google Sheets)."""
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from auth import buat_hash, cek_hash, validasi_password, validasi_username
from laporan import buat_excel, buat_html, hitung_laporan, hitung_stok, stok_negatif, KOL_ABC, KOL_DEAD, KOL_RESTOCK

TZ = ZoneInfo("Asia/Jakarta")
KOLOM_P = ["SKU", "Nama", "Kategori", "Supplier", "Lokasi",
           "Harga_Beli", "Harga_Jual", "Stok_Min", "Stok_Awal"]
KOLOM_T = ["ID", "Waktu", "SKU", "Tipe", "Qty", "Harga_Satuan", "Operator", "Catatan"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
REFRESH = "15s"  # interval auto-refresh dashboard

st.set_page_config(page_title="Inventory Pro", page_icon="📦", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.5rem;}
div[data-testid="stMetric"] {background: rgba(128,128,128,0.10); border: 1px solid rgba(128,128,128,0.30);
  border-radius: 12px; padding: 14px 18px; min-height: 156px; display: flex; flex-direction: column; justify-content: center;}
div[data-testid="stMetricValue"] {font-size: clamp(1.3rem, 1.9vw, 2rem);}
div[data-testid="stMetricValue"] > div {white-space: normal !important; overflow: visible !important;
  text-overflow: clip !important; line-height: 1.2;}
div[data-testid="stMetricLabel"] p {white-space: normal !important;}
</style>
""", unsafe_allow_html=True)


# ---------- util ----------
def secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


def rp(x):
    return "Rp " + f"{x:,.0f}".replace(",", ".")


def rp_ringkas(x):
    a = abs(x)
    for batas, satuan in ((1e12, " T"), (1e9, " M"), (1e6, " jt")):
        if a >= batas:
            v = f"{x / batas:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return f"Rp {v}{satuan}"
    return rp(x)


# ---------- koneksi data ----------
@st.cache_resource
def _koneksi(sid, info_json):
    import json
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(json.loads(info_json), scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(sid)


def get_spreadsheet():
    info, sid = secret("gcp_service_account"), secret("sheet_id")
    if not info or not sid:
        return None  # mode demo (CSV lokal)
    import json
    return _koneksi(sid, json.dumps(dict(info)))


@st.cache_data(ttl=10)
def load_data():
    sh = get_spreadsheet()
    if sh:
        p = pd.DataFrame(sh.worksheet("Produk").get_all_records())
        t = pd.DataFrame(sh.worksheet("Transaksi").get_all_records())
    else:
        p, t = pd.read_csv("data/produk.csv"), pd.read_csv("data/transaksi.csv")
    
    if "Harga" in t.columns and "Harga_Satuan" not in t.columns:
        t.rename(columns={"Harga": "Harga_Satuan"}, inplace=True)
        
    p, t = p.reindex(columns=KOLOM_P), t.reindex(columns=KOLOM_T)
    for c in ["Harga_Beli", "Harga_Jual", "Stok_Min", "Stok_Awal"]:
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0)
    t["Qty"] = pd.to_numeric(t["Qty"], errors="coerce").fillna(0)
    # Harga_Satuan kosong = data lama / bukan transaksi masuk, BUKAN 0 (bonus)
    t["Harga_Satuan"] = pd.to_numeric(t["Harga_Satuan"], errors="coerce")
    t["Waktu"] = pd.to_datetime(t["Waktu"], errors="coerce")
    return p, t


def tambah_transaksi(sku, tipe, qty, harga_satuan, operator, catatan):
    row = [
        uuid.uuid4().hex[:8].upper(),
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        sku,
        tipe,
        int(qty),
        float(harga_satuan),
        str(operator),
        str(catatan)
    ]
    
    sh = get_spreadsheet()
    if sh:
        sh.worksheet("Transaksi").append_row(row, value_input_option="RAW")
    else:
        file_path = "data/transaksi.csv"
        ada_file = os.path.exists(file_path)
        pd.DataFrame([row], columns=KOLOM_T).to_csv(file_path, mode="a", header=not ada_file, index=False)
        
    load_data.clear()
    catat_audit("TRANSAKSI", sku, dict(tipe=tipe, qty=int(qty), harga_satuan=harga_satuan, operator=operator, catatan=catatan))


def tambah_produk(data: dict):
    row = [data[k] for k in KOLOM_P]
    sh = get_spreadsheet()
    if sh:
        sh.worksheet("Produk").append_row(row, value_input_option="RAW")
    else:
        pd.DataFrame([row], columns=KOLOM_P).to_csv("data/produk.csv", mode="a", header=False, index=False)
    load_data.clear()
    catat_audit("PRODUK_TAMBAH", data["SKU"], data)


def diagnosa_secrets():
    st.error("Aplikasi belum terhubung ke Google Sheet.")
    kunci, galat = [], None
    try:
        kunci = list(st.secrets.keys())
    except Exception as e:
        galat = f"{type(e).__name__}: {str(e)[:150]}"
    st.write("**Kunci yang terbaca di Secrets:**", ", ".join(kunci) if kunci else "(kosong)")
    st.write("- `sheet_id`:", "✅ ada" if "sheet_id" in kunci else "❌ tidak ada")
    st.write("- `[gcp_service_account]`:", "✅ ada" if "gcp_service_account" in kunci else "❌ tidak ada")
    if galat:
        st.warning(f"Streamlit gagal membaca Secrets: {galat}")
    st.info("Perbaikan: Manage app → Settings → Secrets → tempel ulang SELURUH isi secrets.toml.")
    st.stop()


# ---------- tema warna ----------
PRESET = {
    "Default": dict(masuk="#2563eb", keluar="#f97316", pendapatan="#2563eb", laba="#16a34a", utama="#6366f1", aksen="#f97316",
                    Aman="#16a34a", Menipis="#f59e0b", Kritis="#ef4444", Habis="#7f1d1d"),
    "Ocean": dict(masuk="#0284c7", keluar="#0d9488", pendapatan="#0369a1", laba="#14b8a6", utama="#0891b2", aksen="#6366f1",
                  Aman="#0d9488", Menipis="#eab308", Kritis="#f43f5e", Habis="#881337"),
    "Sunset": dict(masuk="#e11d48", keluar="#f59e0b", pendapatan="#db2777", laba="#f97316", utama="#e11d48", aksen="#f59e0b",
                   Aman="#65a30d", Menipis="#f59e0b", Kritis="#dc2626", Habis="#7f1d1d"),
    "Forest": dict(masuk="#15803d", keluar="#a16207", pendapatan="#166534", laba="#84cc16", utama="#15803d", aksen="#ca8a04",
                   Aman="#16a34a", Menipis="#eab308", Kritis="#ea580c", Habis="#7f1d1d"),
    "Monokrom": dict(masuk="#334155", keluar="#94a3b8", pendapatan="#1e293b", laba="#64748b", utama="#475569", aksen="#94a3b8",
                     Aman="#64748b", Menipis="#94a3b8", Kritis="#334155", Habis="#0f172a"),
}
LABEL_WARNA = {"masuk": "Barang masuk", "keluar": "Barang keluar", "pendapatan": "Pendapatan", "laba": "Laba",
               "utama": "Warna utama grafik", "aksen": "Warna aksen grafik", "Aman": "Status Aman",
               "Menipis": "Status Menipis", "Kritis": "Status Kritis", "Habis": "Status Habis"}
STATUS = ("Aman", "Menipis", "Kritis", "Habis")


def W(k):
    return st.session_state.get(f"w_{k}", PRESET["Default"][k])


def warna_status():
    return {k: W(k) for k in STATUS}


def terang(h, f=0.6):
    r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * f) for c in (r, g, b))


def warnai_status(df):
    def f(v):
        return f"background-color: {W(v)}40; font-weight: 600" if v in STATUS else ""
    st_ = df.style
    return (st_.map if hasattr(st_, "map") else st_.applymap)(f, subset=["Status"])


def muat_tema():
    try:
        sh = get_spreadsheet()
        if sh:
            data = json.loads(sh.worksheet("Pengaturan").acell("A1").value or "{}")
        elif os.path.exists("data/tema.json"):
            data = json.load(open("data/tema.json", encoding="utf-8"))
        else:
            return {}
        return {k: v for k, v in data.items() if k in PRESET["Default"] and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v))}
    except Exception:
        return {}


def simpan_tema():
    data = {k: W(k) for k in PRESET["Default"]}
    sh = get_spreadsheet()
    if sh:
        try:
            ws = sh.worksheet("Pengaturan")
        except Exception:
            ws = sh.add_worksheet("Pengaturan", rows=5, cols=2)
        ws.update_acell("A1", json.dumps(data))
    else:
        json.dump(data, open("data/tema.json", "w", encoding="utf-8"))
    catat_audit("TEMA_SIMPAN", "tema warna", data)


def terapkan_preset():
    for k, v in PRESET[st.session_state["preset"]].items():
        st.session_state[f"w_{k}"] = v


def reset_tema():
    st.session_state["preset"] = "Default"
    terapkan_preset()


def init_tema():
    if "w_masuk" not in st.session_state:
        for k, v in {**PRESET["Default"], **muat_tema()}.items():
            st.session_state[f"w_{k}"] = v


# ---------- pengguna, login & audit ----------
KOLOM_U = ["Username", "Nama", "Peran", "Hash", "Aktif"]
KOLOM_A = ["Waktu", "Username", "Aksi", "Objek", "Detail"]
MAKS_GAGAL = 5


def _ws(nama, kolom):
    sh = get_spreadsheet()
    try:
        return sh.worksheet(nama)
    except Exception:
        ws = sh.add_worksheet(nama, rows=200, cols=len(kolom))
        ws.append_row(kolom, value_input_option="RAW")
        return ws


def _baca(nama, kolom, csv):
    sh = get_spreadsheet()
    if sh:
        df = pd.DataFrame(_ws(nama, kolom).get_all_records(numericise_ignore=["all"]))
    elif os.path.exists(csv):
        df = pd.read_csv(csv, dtype=str)
    else:
        df = pd.DataFrame()
    return df.reindex(columns=kolom).fillna("").astype(str)


@st.cache_data(ttl=10)
def muat_pengguna():
    return _baca("Pengguna", KOLOM_U, "data/pengguna.csv")


@st.cache_data(ttl=10)
def muat_audit():
    df = _baca("Audit", KOLOM_A, "data/audit.csv")
    df["Waktu"] = pd.to_datetime(df["Waktu"], errors="coerce")
    return df


def tambah_pengguna(d):
    row = [d[k] for k in KOLOM_U]
    if get_spreadsheet():
        _ws("Pengguna", KOLOM_U).append_row(row, value_input_option="RAW")
    else:
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([row], columns=KOLOM_U).to_csv(
            "data/pengguna.csv", mode="a", header=not os.path.exists("data/pengguna.csv"), index=False)
    muat_pengguna.clear()


def ubah_pengguna(username, **perubahan):
    if get_spreadsheet():
        ws = _ws("Pengguna", KOLOM_U)
        baris = ws.col_values(1).index(username) + 1
        for k, v in perubahan.items():
            ws.update_cell(baris, KOLOM_U.index(k) + 1, v)
    else:
        df = muat_pengguna().copy()
        for k, v in perubahan.items():
            df.loc[df["Username"] == username, k] = v
        df.to_csv("data/pengguna.csv", index=False)
    muat_pengguna.clear()


def hapus_pengguna(username):
    if get_spreadsheet():
        ws = _ws("Pengguna", KOLOM_U)
        ws.delete_rows(ws.col_values(1).index(username) + 1)
    else:
        df = muat_pengguna()
        df[df["Username"] != username].to_csv("data/pengguna.csv", index=False)
    muat_pengguna.clear()


def catat_audit(aksi, objek="", detail=None):
    try:
        u = st.session_state.get("user") or {}
        row = [datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), u.get("username", "-"), aksi, str(objek),
               json.dumps(detail, ensure_ascii=False, default=str) if detail else ""]
        if get_spreadsheet():
            _ws("Audit", KOLOM_A).append_row(row, value_input_option="RAW")
        else:
            os.makedirs("data", exist_ok=True)
            pd.DataFrame([row], columns=KOLOM_A).to_csv(
                "data/audit.csv", mode="a", header=not os.path.exists("data/audit.csv"), index=False)
        muat_audit.clear()
    except Exception:
        pass


def _masuk(u):
    st.session_state["user"] = dict(username=u["Username"], nama=u["Nama"] or u["Username"], peran=u["Peran"])
    catat_audit("LOGIN")
    st.rerun()


def _form_password_baru(kunci):
    un = st.text_input("Username", key=f"{kunci}_un").strip().lower()
    nm = st.text_input("Nama lengkap", key=f"{kunci}_nm").strip()
    p1 = st.text_input("Password", type="password", key=f"{kunci}_p1")
    p2 = st.text_input("Ulangi password", type="password", key=f"{kunci}_p2")
    return un, nm, p1, p2


def cek_login():
    if st.session_state.get("user"):
        return
    kode = secret("app_password")
    pengguna = muat_pengguna()
    st.title("📦 Inventory Pro")

    if pengguna.empty:
        if not kode:
            if get_spreadsheet():
                st.error("Belum ada pengguna. Isi `app_password` di Secrets sebagai kode setup, lalu reboot app.")
                st.stop()
            st.session_state["user"] = dict(username="demo", nama="Demo", peran="admin")
            st.rerun()
        st.subheader("Setup awal: buat akun admin pertama")
        with st.form("setup"):
            kd = st.text_input("Kode setup (isi `app_password` di Secrets)", type="password")
            un, nm, p1, p2 = _form_password_baru("setup")
            ok = st.form_submit_button("Buat akun admin", type="primary")
        if ok:
            err = (None if hmac.compare_digest(kd.encode(), str(kode).encode()) else "Kode setup salah.") \
                or validasi_username(un) or validasi_password(p1) or (None if p1 == p2 else "Password tidak sama.")
            if err:
                time.sleep(1)
                st.error(err)
            else:
                tambah_pengguna(dict(Username=un, Nama=nm or un, Peran="admin", Hash=buat_hash(p1), Aktif="Y"))
                _masuk(muat_pengguna().iloc[0])
        st.stop()

    st.subheader("Masuk")
    if st.session_state.get("gagal", 0) >= MAKS_GAGAL:
        st.error("Terlalu banyak percobaan gagal. Muat ulang halaman dan coba lagi nanti.")
        st.stop()
    with st.form("login"):
        un = st.text_input("Username").strip().lower()
        pw = st.text_input("Password", type="password")
        ok = st.form_submit_button("Masuk", type="primary")
    if ok:
        cocok = pengguna[(pengguna["Username"] == un) & (pengguna["Aktif"].str.upper() != "N")]
        if not cocok.empty and cek_hash(pw, cocok.iloc[0]["Hash"]):
            st.session_state["gagal"] = 0
            _masuk(cocok.iloc[0])
        else:
            st.session_state["gagal"] = st.session_state.get("gagal", 0) + 1
            time.sleep(1)
            catat_audit("LOGIN_GAGAL", un or "-")
            st.error("Username atau password salah.")
    st.stop()


def keluar():
    catat_audit("LOGOUT")
    for k in ("user", "gagal"):
        st.session_state.pop(k, None)


# ---------- tab: dashboard ----------
@st.fragment(run_every=REFRESH)
def tab_dashboard():
    p, t = load_data()
    p = hitung_stok(p, t)
    st.caption(f"🟢 Live · diperbarui {datetime.now(TZ):%d %b %Y %H:%M:%S} WIB · auto-refresh {REFRESH}")

    perlu = p[p["Status"].isin(["Kritis", "Habis"])]
    k = st.columns(5)
    k[0].metric("Total SKU", len(p))
    k[1].metric("Total Unit", f"{p['Stok'].sum():,}".replace(",", "."))
    k[2].metric("Nilai Persediaan", rp_ringkas(p["Nilai_Stok"].sum()), help=rp(p["Nilai_Stok"].sum()))
    k[3].metric("Stok Kritis/Habis", len(perlu))
    hari_ini = t[t["Waktu"].dt.date == datetime.now(TZ).date()]
    k[4].metric("Transaksi Hari Ini", len(hari_ini))

    c1, c2 = st.columns([3, 2])
    batas = datetime.now(TZ).replace(tzinfo=None) - timedelta(days=30)
    t30 = t[(t["Waktu"] >= batas) & t["Tipe"].isin(["Masuk", "Keluar"])]
    harian = t30.groupby([t30["Waktu"].dt.date.rename("Tanggal"), "Tipe"])["Qty"].sum().reset_index()
    fig = px.bar(harian, x="Tanggal", y="Qty", color="Tipe", barmode="group",
                 color_discrete_map={"Masuk": W("masuk"), "Keluar": W("keluar")},
                 title="Pergerakan Stok 30 Hari Terakhir")
    fig.update_layout(height=340, margin=dict(t=50, b=10, l=10, r=10), legend_title=None)
    c1.plotly_chart(fig, use_container_width=True)

    stat = p["Status"].value_counts().reset_index()
    fig = px.pie(stat, names="Status", values="count", hole=0.55, color="Status",
                 color_discrete_map=warna_status(), title="Kesehatan Stok")
    fig.update_layout(height=340, margin=dict(t=50, b=10, l=10, r=10))
    c2.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    kat = p.groupby("Kategori")["Nilai_Stok"].sum().reset_index().sort_values("Nilai_Stok")
    fig = px.bar(kat, x="Nilai_Stok", y="Kategori", orientation="h", title="Nilai Persediaan per Kategori",
                 color_discrete_sequence=[W("utama")])
    fig.update_layout(height=320, margin=dict(t=50, b=10, l=10, r=10), xaxis_title="Rp")
    c3.plotly_chart(fig, use_container_width=True)

    keluar = t[(t["Tipe"] == "Keluar") & (t["Waktu"] >= batas)].groupby("SKU")["Qty"].sum()
    top = p.set_index("SKU").join(keluar.rename("Terjual")).dropna(subset=["Terjual"])
    top = top.nlargest(10, "Terjual").reset_index().sort_values("Terjual")
    fig = px.bar(top, x="Terjual", y="Nama", orientation="h", title="Top 10 Produk Keluar (30 hari)",
                 color_discrete_sequence=[W("aksen")])
    fig.update_layout(height=320, margin=dict(t=50, b=10, l=10, r=10), yaxis_title=None)
    c4.plotly_chart(fig, use_container_width=True)

    st.subheader("⚠️ Perlu Restock")
    if perlu.empty:
        st.success("Semua stok dalam kondisi aman.")
    else:
        tampil = perlu.assign(Kekurangan=(perlu["Stok_Min"] * 2 - perlu["Stok"]).clip(lower=0).astype(int))
        st.dataframe(warnai_status(tampil[["SKU", "Nama", "Supplier", "Stok", "Stok_Min", "Status", "Kekurangan"]].sort_values("Stok")), hide_index=True, use_container_width=True)


# ---------- tab: stok ----------
def tab_stok():
    p, t = load_data()
    p = hitung_stok(p, t)
    f = st.columns([2, 2, 2])
    kat = f[0].multiselect("Kategori", sorted(p["Kategori"].unique()))
    sta = f[1].multiselect("Status", ["Aman", "Menipis", "Kritis", "Habis"])
    cari = f[2].text_input("Cari nama / SKU")
    d = p
    if kat:
        d = d[d["Kategori"].isin(kat)]
    if sta:
        d = d[d["Status"].isin(sta)]
    if cari:
        d = d[d["Nama"].str.contains(cari, case=False) | d["SKU"].str.contains(cari, case=False)]
    st.dataframe(
        warnai_status(d[["SKU", "Nama", "Kategori", "Lokasi", "Stok", "Stok_Min", "Level", "Status", "Harga_Rata2", "Harga_Jual", "Nilai_Stok"]]),
        hide_index=True, use_container_width=True,
        column_config={
            "Level": st.column_config.ProgressColumn("Level Stok", min_value=0, max_value=100, format="%d%%"),
            "Harga_Rata2": st.column_config.NumberColumn("HPP rata-rata", format="Rp %d"),
            "Harga_Jual": st.column_config.NumberColumn("Harga Jual", format="Rp %d"),
            "Nilai_Stok": st.column_config.NumberColumn("Nilai Stok", format="Rp %d"),
            "Stok_Min": "Min",
        })
    st.download_button("⬇️ Unduh CSV", d.to_csv(index=False).encode(), "stok.csv", "text/csv")


# ---------- edit / hapus transaksi (khusus admin) ----------
def _kolom_sheet(header, nama):
    if nama in header:
        return header.index(nama) + 1
    if nama == "Harga_Satuan" and "Harga" in header:
        return header.index("Harga") + 1
    raise ValueError(f"Kolom '{nama}' tidak ada di sheet Transaksi")


def _ubah_baris_transaksi(id_, perubahan):
    sh = get_spreadsheet()
    if sh:
        from gspread.utils import rowcol_to_a1
        ws = sh.worksheet("Transaksi")
        header = ws.row_values(1)
        baris = ws.col_values(1).index(id_) + 1
        for k, v in perubahan.items():
            ws.update(range_name=rowcol_to_a1(baris, _kolom_sheet(header, k)), values=[[v]], raw=True)
    else:
        df = pd.read_csv("data/transaksi.csv", dtype=str)
        m = df["ID"].str.upper() == id_
        for k, v in perubahan.items():
            df.loc[m, k if k in df.columns else "Harga"] = str(v)
        df.to_csv("data/transaksi.csv", index=False)
    load_data.clear()


def _hapus_baris_transaksi(id_):
    sh = get_spreadsheet()
    if sh:
        ws = sh.worksheet("Transaksi")
        ws.delete_rows(ws.col_values(1).index(id_) + 1)
    else:
        df = pd.read_csv("data/transaksi.csv", dtype=str)
        df[df["ID"].str.upper() != id_].to_csv("data/transaksi.csv", index=False)
    load_data.clear()


def _stok_aman_setelah(p, t, id_, qty_baru=None):
    """None jika aman; pesan error jika perubahan membuat stok negatif di riwayat."""
    t2 = t.copy()
    m = t2["ID"].astype(str).str.upper() == id_
    if qty_baru is None:
        t2 = t2[~m]
    else:
        t2.loc[m, "Qty"] = qty_baru
    baru = set(stok_negatif(p, t2)) - set(stok_negatif(p, t))
    if baru:
        return ("Ditolak: perubahan ini membuat stok negatif pada produk " + ", ".join(sorted(baru))
                + ". Koreksi transaksi keluar terkait terlebih dahulu.")
    return None


def kelola_transaksi(p, t, r, admin):
    pesan = st.session_state.pop("pesan_kelola", None)
    if pesan:
        st.toast(pesan)
    with st.expander("⚙️ Kelola / Edit / Hapus Transaksi"):
        if not admin:
            st.info("Hanya admin yang dapat mengedit atau menghapus transaksi.")
            return
        if r.empty:
            st.info("Tidak ada riwayat transaksi untuk dikelola.")
            return
        daftar = r.head(200)
        st.caption("Menampilkan 200 transaksi terbaru.")
        label = daftar.apply(lambda x: f"[{x['ID']}] {x['Waktu']} - {x['Nama']} ({x['Tipe']} Qty: {x['Qty']})", axis=1)
        pilih = st.selectbox("Pilih transaksi yang ingin dikelola:", label)
        id_ = pilih.split("]")[0].lstrip("[").strip().upper()
        trx = daftar[daftar["ID"].astype(str).str.upper() == id_].iloc[0]
        tipe = trx["Tipe"]
        harga_ada = pd.notna(trx["Harga_Satuan"])
        st.write(f"**Terpilih:** `{id_}` · `{trx['SKU']}` · {tipe}")
        tab_e, tab_h = st.tabs(["✏️ Edit", "🗑️ Hapus"])

        with tab_e:
            with st.form(f"form_edit_{id_}"):
                qty_baru = st.number_input("Qty baru", value=int(trx["Qty"]), step=1)
                harga_baru = 0.0
                if tipe == "Masuk":
                    harga_baru = st.number_input(
                        "Harga beli satuan (Rp)", min_value=0.0, step=1000.0,
                        value=float(trx["Harga_Satuan"]) if harga_ada else 0.0,
                        help="0 = barang bonus. Pada data lama yang harganya kosong, biarkan 0 agar tetap kosong.")
                catatan_baru = st.text_input("Catatan", value="" if pd.isna(trx["Catatan"]) else str(trx["Catatan"]))
                simpan = st.form_submit_button("Simpan perubahan", type="primary")
            if simpan:
                if tipe != "Penyesuaian" and qty_baru <= 0:
                    err = "Qty harus lebih dari 0."
                elif tipe == "Penyesuaian" and qty_baru == 0:
                    err = "Qty penyesuaian tidak boleh 0."
                else:
                    err = _stok_aman_setelah(p, t, id_, int(qty_baru))
                if err:
                    st.error(err)
                else:
                    ubah = {"Qty": int(qty_baru), "Catatan": catatan_baru}
                    if tipe == "Masuk" and (harga_ada or harga_baru > 0):
                        ubah["Harga_Satuan"] = float(harga_baru)
                    _ubah_baris_transaksi(id_, ubah)
                    catat_audit("TRANSAKSI_EDIT", id_, dict(
                        sku=trx["SKU"], sesudah=ubah,
                        sebelum=dict(qty=int(trx["Qty"]), harga=float(trx["Harga_Satuan"]) if harga_ada else None,
                                     catatan="" if pd.isna(trx["Catatan"]) else str(trx["Catatan"]))))
                    st.session_state["pesan_kelola"] = f"Transaksi {id_} diperbarui."
                    st.rerun()

        with tab_h:
            st.warning("Menghapus transaksi mengembalikan stok dan biaya rata-rata seolah transaksi itu tidak pernah ada.")
            if st.button("🗑️ Hapus transaksi ini", type="primary", key=f"hapus_trx_{id_}"):
                err = _stok_aman_setelah(p, t, id_)
                if err:
                    st.error(err)
                else:
                    _hapus_baris_transaksi(id_)
                    catat_audit("TRANSAKSI_HAPUS", id_, dict(
                        sku=trx["SKU"], tipe=tipe, qty=int(trx["Qty"]),
                        harga=float(trx["Harga_Satuan"]) if harga_ada else None))
                    st.session_state["pesan_kelola"] = f"Transaksi {id_} dihapus."
                    st.rerun()


# ---------- tab: transaksi ----------
def tab_transaksi(operator, admin=False):
    p, t = load_data()
    p = hitung_stok(p, t)
    st.subheader("Catat Transaksi")
    
    with st.form("form_trx", clear_on_submit=True):
        c = st.columns([3, 2, 1])
        pilih = c[0].selectbox("Produk", p["SKU"] + " · " + p["Nama"])
        tipe = c[1].selectbox("Tipe", ["Masuk", "Keluar"] + (["Penyesuaian"] if admin else []))
        qty = c[2].number_input("Qty", value=1, step=1, help="Penyesuaian boleh negatif.")

        c2 = st.columns([2, 2, 3])
        is_bonus = c2[0].checkbox("🎁 Barang Bonus (Harga Beli Rp 0)", value=False)
        harga_masuk_baru = c2[1].number_input("Harga Beli Satuan (Rp)", min_value=0.0, value=0.0, step=1000.0,
                                             disabled=is_bonus,
                                             help="Isi harga beli satuan baru khusus jika barang Masuk.")
        cat = c2[2].text_input("Catatan")
        
        st.caption(f"✍️ Operator pencatat: **{operator}**")
        kirim = st.form_submit_button("Simpan Transaksi", type="primary")
        
    if kirim:
        sku = pilih.split(" · ")[0]
        stok_now = int(p.loc[p["SKU"] == sku, "Stok"].iloc[0])
        final_harga = 0.0 if is_bonus else float(harga_masuk_baru)

        if tipe != "Penyesuaian" and qty <= 0:
            st.error("Qty harus lebih dari 0.")
        elif tipe == "Penyesuaian" and qty == 0:
            st.error("Qty penyesuaian tidak boleh 0.")
        elif tipe == "Keluar" and qty > stok_now:
            st.error(f"Stok tidak cukup. Stok saat ini: {stok_now}.")
        elif tipe == "Penyesuaian" and stok_now + qty < 0:
            st.error("Penyesuaian membuat stok negatif.")
        elif tipe == "Masuk" and not is_bonus and final_harga <= 0:
            st.error("Harga beli tidak boleh 0 untuk barang masuk berbayar. Jika gratis, centang 'Barang Bonus'.")
        else:
            h_satuan = final_harga if tipe == "Masuk" else 0.0
            tambah_transaksi(sku, tipe, qty, h_satuan, operator, cat)
            st.success(f"Transaksi berhasil disimpan oleh {operator}.")
            st.rerun()

    st.subheader("Riwayat Terbaru")
    r = t.merge(p[["SKU", "Nama"]], on="SKU", how="left").sort_values("Waktu", ascending=False)
    ft = st.multiselect("Filter tipe", ["Masuk", "Keluar", "Penyesuaian"], key="ft")
    if ft:
        r = r[r["Tipe"].isin(ft)]
    
    # Format mata uang menggunakan titik pemisah ribuan (Rp 150.000)
    st.dataframe(r[["Waktu", "ID", "SKU", "Nama", "Tipe", "Qty", "Harga_Satuan", "Operator", "Catatan"]].head(100),
                 hide_index=True, use_container_width=True,
                 column_config={"Harga_Satuan": st.column_config.NumberColumn("Harga Masuk", format="Rp %d")})

    kelola_transaksi(p, t, r, admin)


# ---------- tab: produk baru ----------
def tab_produk():
    p, _ = load_data()
    st.subheader("Tambah Produk Baru")
    with st.form("form_produk", clear_on_submit=True):
        c = st.columns(3)
        sku = c[0].text_input("SKU (unik)").strip().upper()
        nama = c[1].text_input("Nama produk")
        kat = c[2].text_input("Kategori")
        c = st.columns(3)
        sup = c[0].text_input("Supplier")
        lok = c[1].text_input("Lokasi rak")
        smin = c[2].number_input("Stok minimum", min_value=0, value=10)
        c = st.columns(3)
        hb = c[0].number_input("Harga beli (Rp)", min_value=0, step=1000)
        hj = c[1].number_input("Harga jual (Rp)", min_value=0, step=1000)
        awal = c[2].number_input("Stok awal", min_value=0, value=0)
        kirim = st.form_submit_button("Tambah", type="primary")
    if kirim:
        if not sku or not nama:
            st.error("SKU dan nama wajib diisi.")
        elif sku in set(p["SKU"]):
            st.error("SKU sudah ada.")
        else:
            tambah_produk(dict(SKU=sku, Nama=nama, Kategori=kat, Supplier=sup, Lokasi=lok,
                               Harga_Beli=hb, Harga_Jual=hj, Stok_Min=smin, Stok_Awal=awal))
            st.success("Produk ditambahkan.")
            st.rerun()


# ---------- tab: laporan & KPI ----------
def tab_laporan():
    p, t = load_data()
    p = hitung_stok(p, t)
    hari = datetime.now(TZ).date()
    c = st.columns([2, 1, 1])
    rentang = c[0].date_input("Periode laporan", value=(hari - timedelta(days=29), hari), key="rentang")
    lead = c[1].number_input("Lead time supplier (hari)", 1, 60, 7)
    cover = c[2].number_input("Target cakupan stok (hari)", 1, 90, 14)
    if not (isinstance(rentang, (tuple, list)) and len(rentang) == 2):
        st.info("Pilih tanggal mulai dan tanggal selesai.")
        return
    mulai = pd.Timestamp(rentang[0])
    akhir = pd.Timestamp(rentang[1]) + pd.Timedelta(days=1)
    h = hitung_laporan(p, t, mulai, akhir, lead, cover)
    k = h["kpi"]

    def delta(a, b):
        return f"{(a - b) / b * 100:+.1f}% vs periode lalu" if b else None

    st.subheader("KPI Utama")
    m = st.columns(4)
    m[0].metric("Pendapatan", rp_ringkas(k["pendapatan"]), delta(k["pendapatan"], k["pendapatan_prev"]))
    m[1].metric("HPP", rp_ringkas(k["hpp"]), delta(k["hpp"], k["hpp_prev"]), delta_color="off")
    m[2].metric("Laba Kotor", rp_ringkas(k["laba"]), delta(k["laba"], k["laba_prev"]))
    m[3].metric("Margin Kotor", f"{k['margin']:.1f}%", f"{k['margin'] - k['margin_prev']:+.1f} poin")
    m = st.columns(4)
    m[0].metric("Inventory Turnover", f"{k['turnover']:.2f}x", f"setahun ≈ {k['turnover_tahunan']:.1f}x", delta_color="off")
    m[1].metric("Days of Inventory", "-" if pd.isna(k["doi"]) else f"{k['doi']:.0f} hari")
    m[2].metric("Sell-Through", f"{k['sell_through']:.1f}%")
    m[3].metric("Nilai Persediaan", rp_ringkas(k["nilai_stok"]))
    m = st.columns(4)
    m[0].metric("Stockout Rate", f"{k['stockout']:.1f}%")
    m[1].metric("Dead Stock", rp_ringkas(k["dead_nilai"]), f"{k['dead_sku']} SKU", delta_color="off")
    m[2].metric("Unit Masuk / Keluar", f"{k['unit_masuk']:,.0f} / {k['unit_keluar']:,.0f}".replace(",", "."))
    m[3].metric("Perlu Restock", f"{k['restock_sku']} SKU", f"≈ {rp_ringkas(k['restock_biaya'])}", delta_color="off")

    st.subheader("Scorecard KPI vs Target")
    st.dataframe(h["skor"], hide_index=True, use_container_width=True)

    c1, c2 = st.columns(2)
    fig = px.line(h["harian"], x="Tanggal", y=["Pendapatan", "Laba"], markers=True, title="Tren Pendapatan & Laba Harian",
                  color_discrete_sequence=[W("pendapatan"), W("laba")])
    fig.update_layout(height=340, margin=dict(t=50, b=10, l=10, r=10), legend_title=None, yaxis_title="Rp")
    c1.plotly_chart(fig, use_container_width=True)
    fig = px.bar(h["kategori"].sort_values("Laba"), x="Laba", y="Kategori", orientation="h", color="Margin_%",
                 color_continuous_scale=[terang(W("utama")), W("utama")], title="Laba & Margin per Kategori")
    fig.update_layout(height=340, margin=dict(t=50, b=10, l=10, r=10), coloraxis_colorbar_title="Margin %")
    c2.plotly_chart(fig, use_container_width=True)

    st.subheader("Analisis ABC (Pareto Pendapatan)")
    abc = h["abc"]
    if abc.empty:
        st.info("Belum ada penjualan pada periode ini.")
    else:
        warna = {"A": W("utama"), "B": W("aksen"), "C": "#94a3b8"}
        fig = go.Figure()
        fig.add_bar(x=abc["SKU"], y=abc["Pendapatan"], marker_color=abc["Kelas"].map(warna), name="Pendapatan",
                    customdata=abc[["Nama", "Kelas"]], hovertemplate="%{customdata[0]}<br>Kelas %{customdata[1]}<br>Rp %{y:,.0f}<extra></extra>")
        fig.add_scatter(x=abc["SKU"], y=abc["Kum_%"], yaxis="y2", mode="lines+markers", name="Kumulatif %", line=dict(color=W("Kritis")))
        fig.update_layout(height=360, margin=dict(t=30, b=10, l=10, r=10), yaxis2=dict(overlaying="y", side="right", range=[0, 105], title="%"),
                          yaxis_title="Rp", legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, use_container_width=True)
        ringkas = abc.groupby("Kelas").agg(Jumlah_SKU=("SKU", "count"), Pendapatan=("Pendapatan", "sum")).reset_index()
        st.caption("A = ±80% pendapatan (prioritas jaga stok) · B = 15% berikutnya · C = sisanya")
        st.dataframe(ringkas, hide_index=True, use_container_width=True,
                     column_config={"Pendapatan": st.column_config.NumberColumn(format="Rp %d")})

    with st.expander(f"🛒 Saran Restock ({len(h['restock'])} SKU)", expanded=not h["restock"].empty):
        st.dataframe(h["restock"][KOL_RESTOCK], hide_index=True, use_container_width=True,
                     column_config={"Est_Biaya": st.column_config.NumberColumn("Est. Biaya", format="Rp %d"),
                                    "Rata2_Harian": st.column_config.NumberColumn("Rata2/hari", format="%.1f"),
                                    "Cakupan_Hari": st.column_config.NumberColumn("Cakupan (hari)", format="%.1f")})
    with st.expander(f"🧊 Dead Stock ({len(h['dead'])} SKU)"):
        st.dataframe(h["dead"][KOL_DEAD], hide_index=True, use_container_width=True,
                     column_config={"Nilai_Stok": st.column_config.NumberColumn("Nilai Stok", format="Rp %d")})

    st.subheader("Unduh Laporan")
    d = st.columns(2)
    d[0].download_button("⬇️ Excel (6 sheet)", buat_excel(h), f"laporan_inventory_{hari:%Y%m%d}.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d[1].download_button("⬇️ HTML (siap cetak / simpan PDF)", buat_html(h).encode("utf-8"),
                         f"laporan_inventory_{hari:%Y%m%d}.html", "text/html")


# ---------- tab: pengguna (admin) ----------
def tab_pengguna():
    me = st.session_state["user"]["username"]
    pesan = st.session_state.pop("pesan_pengguna", None)
    if pesan:
        st.success(pesan)
    pengguna = muat_pengguna()
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Tambah pengguna")
        with st.form("tambah_user", clear_on_submit=True):
            un, nm, p1, p2 = _form_password_baru("baru")
            peran = st.selectbox("Peran", ["staf", "admin"])
            ok = st.form_submit_button("Tambah", type="primary")
        if ok:
            err = validasi_username(un) or validasi_password(p1) or (None if p1 == p2 else "Password tidak sama.")
            if not err and un in set(pengguna["Username"]):
                err = "Username sudah dipakai."
            if err:
                st.error(err)
            else:
                tambah_pengguna(dict(Username=un, Nama=nm or un, Peran=peran, Hash=buat_hash(p1), Aktif="Y"))
                catat_audit("PENGGUNA_TAMBAH", un, dict(peran=peran))
                st.success(f"Pengguna {un} ditambahkan.")

    with c2:
        st.subheader("Ubah pengguna")
        if not pengguna.empty:
            pilih = st.selectbox("Pengguna", pengguna["Username"], key="ubah_pilih")
            row = pengguna[pengguna["Username"] == pilih].iloc[0]
            aktif_lama = row["Aktif"].upper() != "N"
            with st.form("ubah_user"):
                peran_baru = st.selectbox("Peran", ["staf", "admin"], index=1 if row["Peran"] == "admin" else 0, key=f"peran_{pilih}")
                nama_baru = st.text_input("Nama lengkap", value=row["Nama"], key=f"nama_{pilih}").strip()
                aktif_baru = st.checkbox("Akun aktif", value=aktif_lama, key=f"aktif_{pilih}")
                pw_baru = st.text_input("Password baru (kosongkan jika tidak diganti)", type="password", key=f"pw_{pilih}")
                ok2 = st.form_submit_button("Simpan perubahan")
            if ok2:
                err, ubah, log = None, {}, {}
                if pilih == me and (peran_baru != "admin" or not aktif_baru):
                    err = "Anda tidak bisa menurunkan atau menonaktifkan akun sendiri."
                admin_aktif = pengguna[(pengguna["Peran"] == "admin") & (pengguna["Aktif"].str.upper() != "N") & (pengguna["Username"] != pilih)]
                if not err and len(admin_aktif) == 0 and not (peran_baru == "admin" and aktif_baru):
                    err = "Harus tersisa minimal satu admin aktif."
                if not err and pw_baru:
                    err = validasi_password(pw_baru)
                if err:
                    st.error(err)
                else:
                    if nama_baru and nama_baru != row["Nama"]:
                        ubah["Nama"], log["nama"] = nama_baru, [row["Nama"], nama_baru]
                        if pilih == me:
                            st.session_state["user"]["nama"] = nama_baru
                    if peran_baru != row["Peran"]:
                        ubah["Peran"], log["peran"] = peran_baru, [row["Peran"], peran_baru]
                    if aktif_baru != aktif_lama:
                        ubah["Aktif"], log["aktif"] = ("Y" if aktif_baru else "N"), [aktif_lama, aktif_baru]
                    if pw_baru:
                        ubah["Hash"], log["password_direset"] = buat_hash(pw_baru), True
                    if not ubah:
                        st.info("Tidak ada perubahan.")
                    else:
                        ubah_pengguna(pilih, **ubah)
                        catat_audit("PENGGUNA_UBAH", pilih, log)
                        st.success("Perubahan disimpan.")

            with st.expander("🗑️ Hapus pengguna"):
                st.caption("Penghapusan permanen. Riwayat transaksi dan log audit tetap tersimpan.")
                konfirmasi = st.text_input(f"Ketik `{pilih}` untuk konfirmasi", key=f"hapus_{pilih}")
                if st.button("Hapus permanen", key=f"btn_hapus_{pilih}"):
                    admin_lain = pengguna[(pengguna["Peran"] == "admin") & (pengguna["Aktif"].str.upper() != "N") & (pengguna["Username"] != pilih)]
                    if pilih == me:
                        st.error("Anda tidak bisa menghapus akun sendiri.")
                    elif konfirmasi.strip() != pilih:
                        st.error("Konfirmasi tidak cocok dengan username.")
                    elif row["Peran"] == "admin" and aktif_lama and len(admin_lain) == 0:
                        st.error("Harus tersisa minimal satu admin aktif.")
                    else:
                        hapus_pengguna(pilih)
                        catat_audit("PENGGUNA_HAPUS", pilih, dict(nama=row["Nama"], peran=row["Peran"]))
                        st.session_state["pesan_pengguna"] = f"Pengguna {pilih} dihapus."
                        st.rerun()

    st.subheader("Daftar pengguna")
    st.dataframe(muat_pengguna()[["Username", "Nama", "Peran", "Aktif"]], hide_index=True, use_container_width=True)


# ---------- tab: log audit (admin) ----------
def tab_audit():
    a = muat_audit()
    st.subheader("Log Audit")
    if a.empty:
        st.info("Belum ada catatan aktivitas.")
        return
    f = st.columns(3)
    u = f[0].multiselect("Pengguna", sorted(a["Username"].unique()))
    k = f[1].multiselect("Aksi", sorted(a["Aksi"].unique()))
    q = f[2].text_input("Cari di objek / detail")
    if u:
        a = a[a["Username"].isin(u)]
    if k:
        a = a[a["Aksi"].isin(k)]
    if q:
        a = a[a["Objek"].str.contains(q, case=False) | a["Detail"].str.contains(q, case=False)]
    a = a.sort_values("Waktu", ascending=False)
    st.caption(f"{len(a)} catatan (menampilkan 500 terbaru)")
    st.dataframe(a.head(500), hide_index=True, use_container_width=True)
    st.download_button("⬇️ Unduh CSV", a.to_csv(index=False).encode(), "log_audit.csv", "text/csv")


# ---------- main ----------
if not get_spreadsheet() and not os.path.exists("data/produk.csv"):
    diagnosa_secrets()

cek_login()
init_tema()
user = st.session_state["user"]
admin = user["peran"] == "admin"
operator = user["nama"]
with st.sidebar:
    st.title("📦 Inventory Pro")
    st.markdown(f"👤 **{user['nama']}**")
    st.caption("Administrator" if admin else "Staf gudang")
    if st.button("🚪 Keluar"):
        keluar()
        st.rerun()
    if get_spreadsheet():
        if admin:
            st.caption("🟢 Data tersinkron")
    else:
        st.warning("Mode DEMO (data CSV lokal). Isi secrets untuk menyambung ke Google Sheet.")
    if st.button("🔄 Refresh sekarang"):
        load_data.clear()
        st.rerun()
    with st.expander("🎨 Tampilan & warna"):
        st.selectbox("Tema warna", list(PRESET), key="preset", on_change=terapkan_preset)
        for k, label in LABEL_WARNA.items():
            st.color_picker(label, key=f"w_{k}")
        b1, b2 = st.columns(2)
        if b2.button("↩️ Reset", on_click=reset_tema):
            pass
        if admin and b1.button("💾 Simpan"):
            try:
                simpan_tema()
                st.success("Tersimpan sebagai default.")
            except Exception as e:
                st.error(f"Gagal menyimpan: {type(e).__name__}")

st.title("Sistem Inventory")
daftar = [
    ("📊 Dashboard", tab_dashboard, False),
    ("📈 Laporan & KPI", tab_laporan, True),
    ("📋 Stok", tab_stok, False),
    ("🔁 Transaksi", lambda: tab_transaksi(operator, admin), False),
    ("➕ Produk Baru", tab_produk, True),
    ("👥 Pengguna", tab_pengguna, True),
    ("🧾 Log Audit", tab_audit, True),
]
daftar = [d for d in daftar if admin or not d[2]]
for wadah, d in zip(st.tabs([d[0] for d in daftar]), daftar):
    with wadah:
        d[1]()
