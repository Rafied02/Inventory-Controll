"""Inventory Pro - sistem inventory realtime & interaktif (Streamlit + Google Sheets)."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

TZ = ZoneInfo("Asia/Jakarta")
KOLOM_P = ["SKU", "Nama", "Kategori", "Supplier", "Lokasi",
           "Harga_Beli", "Harga_Jual", "Stok_Min", "Stok_Awal"]
KOLOM_T = ["ID", "Waktu", "SKU", "Tipe", "Qty", "Operator", "Catatan"]
WARNA = {"Aman": "#16a34a", "Menipis": "#f59e0b", "Kritis": "#ef4444", "Habis": "#7f1d1d"}
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
REFRESH = "15s"  # interval auto-refresh dashboard

st.set_page_config(page_title="Inventory Pro", page_icon="📦", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.5rem;}
div[data-testid="stMetric"] {background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:14px 18px;}
@media (prefers-color-scheme: dark){
  div[data-testid="stMetric"] {background:#1e293b;border-color:#334155;}
}
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


# ---------- koneksi data ----------
@st.cache_resource
def get_spreadsheet():
    info, sid = secret("gcp_service_account"), secret("sheet_id")
    if not info or not sid:
        return None  # mode demo (CSV lokal)
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(dict(info), scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(sid)


@st.cache_data(ttl=10)  # data dianggap "realtime" = maksimal 10 detik
def load_data():
    sh = get_spreadsheet()
    if sh:
        p = pd.DataFrame(sh.worksheet("Produk").get_all_records())
        t = pd.DataFrame(sh.worksheet("Transaksi").get_all_records())
    else:
        p, t = pd.read_csv("produk.csv"), pd.read_csv("transaksi.csv")
    p, t = p.reindex(columns=KOLOM_P), t.reindex(columns=KOLOM_T)
    for c in ["Harga_Beli", "Harga_Jual", "Stok_Min", "Stok_Awal"]:
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0)
    t["Qty"] = pd.to_numeric(t["Qty"], errors="coerce").fillna(0)
    t["Waktu"] = pd.to_datetime(t["Waktu"], errors="coerce")
    return p, t


def hitung_stok(p, t):
    delta = np.where(t["Tipe"] == "Keluar", -t["Qty"], t["Qty"])
    net = pd.Series(delta, index=t.index).groupby(t["SKU"]).sum()
    p = p.copy()
    p["Stok"] = (p["Stok_Awal"] + p["SKU"].map(net).fillna(0)).astype(int)
    p["Nilai_Stok"] = p["Stok"] * p["Harga_Beli"]
    p["Status"] = np.select(
        [p["Stok"] <= 0, p["Stok"] <= p["Stok_Min"], p["Stok"] <= p["Stok_Min"] * 1.5],
        ["Habis", "Kritis", "Menipis"], "Aman")
    p["Level"] = (p["Stok"] / (p["Stok_Min"].replace(0, 1) * 3)).clip(0, 1) * 100
    return p


def tambah_transaksi(sku, tipe, qty, operator, catatan):
    row = [uuid.uuid4().hex[:8].upper(), datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
           sku, tipe, int(qty), operator, catatan]
    sh = get_spreadsheet()
    if sh:
        sh.worksheet("Transaksi").append_row(row, value_input_option="USER_ENTERED")
    else:
        pd.DataFrame([row], columns=KOLOM_T).to_csv("transaksi.csv", mode="a", header=False, index=False)
    load_data.clear()


def tambah_produk(data: dict):
    row = [data[k] for k in KOLOM_P]
    sh = get_spreadsheet()
    if sh:
        sh.worksheet("Produk").append_row(row, value_input_option="USER_ENTERED")
    else:
        pd.DataFrame([row], columns=KOLOM_P).to_csv("produk.csv", mode="a", header=False, index=False)
    load_data.clear()


# ---------- login (opsional) ----------
def cek_login():
    pw = secret("app_password")
    if not pw or st.session_state.get("auth"):
        return
    st.title("📦 Inventory Pro")
    masuk = st.text_input("Password", type="password")
    if st.button("Masuk", type="primary"):
        if masuk == pw:
            st.session_state["auth"] = True
            st.rerun()
        else:
            st.error("Password salah")
    st.stop()


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
    k[2].metric("Nilai Persediaan", rp(p["Nilai_Stok"].sum()))
    k[3].metric("Stok Kritis/Habis", len(perlu))
    hari_ini = t[t["Waktu"].dt.date == datetime.now(TZ).date()]
    k[4].metric("Transaksi Hari Ini", len(hari_ini))

    c1, c2 = st.columns([3, 2])
    batas = datetime.now(TZ).replace(tzinfo=None) - timedelta(days=30)
    t30 = t[(t["Waktu"] >= batas) & t["Tipe"].isin(["Masuk", "Keluar"])]
    harian = t30.groupby([t30["Waktu"].dt.date.rename("Tanggal"), "Tipe"])["Qty"].sum().reset_index()
    fig = px.bar(harian, x="Tanggal", y="Qty", color="Tipe", barmode="group",
                 color_discrete_map={"Masuk": "#2563eb", "Keluar": "#f97316"},
                 title="Pergerakan Stok 30 Hari Terakhir")
    fig.update_layout(height=340, margin=dict(t=50, b=10, l=10, r=10), legend_title=None)
    c1.plotly_chart(fig, use_container_width=True)

    stat = p["Status"].value_counts().reset_index()
    fig = px.pie(stat, names="Status", values="count", hole=0.55, color="Status",
                 color_discrete_map=WARNA, title="Kesehatan Stok")
    fig.update_layout(height=340, margin=dict(t=50, b=10, l=10, r=10))
    c2.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    kat = p.groupby("Kategori")["Nilai_Stok"].sum().reset_index().sort_values("Nilai_Stok")
    fig = px.bar(kat, x="Nilai_Stok", y="Kategori", orientation="h", title="Nilai Persediaan per Kategori",
                 color_discrete_sequence=["#6366f1"])
    fig.update_layout(height=320, margin=dict(t=50, b=10, l=10, r=10), xaxis_title="Rp")
    c3.plotly_chart(fig, use_container_width=True)

    keluar = t[(t["Tipe"] == "Keluar") & (t["Waktu"] >= batas)].groupby("SKU")["Qty"].sum()
    top = p.set_index("SKU").join(keluar.rename("Terjual")).dropna(subset=["Terjual"])
    top = top.nlargest(10, "Terjual").reset_index().sort_values("Terjual")
    fig = px.bar(top, x="Terjual", y="Nama", orientation="h", title="Top 10 Produk Keluar (30 hari)",
                 color_discrete_sequence=["#f97316"])
    fig.update_layout(height=320, margin=dict(t=50, b=10, l=10, r=10), yaxis_title=None)
    c4.plotly_chart(fig, use_container_width=True)

    st.subheader("⚠️ Perlu Restock")
    if perlu.empty:
        st.success("Semua stok dalam kondisi aman.")
    else:
        tampil = perlu.assign(Kekurangan=(perlu["Stok_Min"] * 2 - perlu["Stok"]).clip(lower=0).astype(int))
        st.dataframe(tampil[["SKU", "Nama", "Supplier", "Stok", "Stok_Min", "Status", "Kekurangan"]]
                     .sort_values("Stok"), hide_index=True, use_container_width=True)


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
        d[["SKU", "Nama", "Kategori", "Lokasi", "Stok", "Stok_Min", "Level", "Status", "Harga_Beli", "Harga_Jual", "Nilai_Stok"]],
        hide_index=True, use_container_width=True,
        column_config={
            "Level": st.column_config.ProgressColumn("Level Stok", min_value=0, max_value=100, format="%d%%"),
            "Harga_Beli": st.column_config.NumberColumn("Harga Beli", format="Rp %d"),
            "Harga_Jual": st.column_config.NumberColumn("Harga Jual", format="Rp %d"),
            "Nilai_Stok": st.column_config.NumberColumn("Nilai Stok", format="Rp %d"),
            "Stok_Min": "Min",
        })
    st.download_button("⬇️ Unduh CSV", d.to_csv(index=False).encode(), "stok.csv", "text/csv")


# ---------- tab: transaksi ----------
def tab_transaksi(operator):
    p, t = load_data()
    p = hitung_stok(p, t)
    st.subheader("Catat Transaksi")
    with st.form("form_trx", clear_on_submit=True):
        c = st.columns([3, 2, 1, 3])
        pilih = c[0].selectbox("Produk", p["SKU"] + " · " + p["Nama"])
        tipe = c[1].selectbox("Tipe", ["Masuk", "Keluar", "Penyesuaian"])
        qty = c[2].number_input("Qty", value=1, step=1,
                                help="Penyesuaian boleh negatif (koreksi stock opname).")
        cat = c[3].text_input("Catatan")
        kirim = st.form_submit_button("Simpan", type="primary")
    if kirim:
        sku = pilih.split(" · ")[0]
        stok_now = int(p.loc[p["SKU"] == sku, "Stok"].iloc[0])
        if tipe != "Penyesuaian" and qty <= 0:
            st.error("Qty harus lebih dari 0.")
        elif tipe == "Penyesuaian" and qty == 0:
            st.error("Qty penyesuaian tidak boleh 0.")
        elif tipe == "Keluar" and qty > stok_now:
            st.error(f"Stok tidak cukup. Stok saat ini: {stok_now}.")
        elif tipe == "Penyesuaian" and stok_now + qty < 0:
            st.error("Penyesuaian membuat stok negatif.")
        else:
            tambah_transaksi(sku, tipe, qty, operator, cat)
            st.success("Transaksi tersimpan.")
            st.rerun()

    st.subheader("Riwayat Terbaru")
    r = t.merge(p[["SKU", "Nama"]], on="SKU", how="left").sort_values("Waktu", ascending=False)
    ft = st.multiselect("Filter tipe", ["Masuk", "Keluar", "Penyesuaian"], key="ft")
    if ft:
        r = r[r["Tipe"].isin(ft)]
    st.dataframe(r[["Waktu", "ID", "SKU", "Nama", "Tipe", "Qty", "Operator", "Catatan"]].head(100),
                 hide_index=True, use_container_width=True)


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


# ---------- main ----------
cek_login()
with st.sidebar:
    st.title("📦 Inventory Pro")
    operator = st.text_input("Nama operator", value="Admin")
    if get_spreadsheet():
        st.success("Terhubung ke Google Sheet")
    else:
        st.warning("Mode DEMO (data CSV lokal). Isi secrets untuk menyambung ke Google Sheet.")
    if st.button("🔄 Refresh sekarang"):
        load_data.clear()
        st.rerun()

st.title("Sistem Inventory")
t1, t2, t3, t4 = st.tabs(["📊 Dashboard", "📋 Stok", "🔁 Transaksi", "➕ Produk Baru"])
with t1:
    tab_dashboard()
with t2:
    tab_stok()
with t3:
    tab_transaksi(operator)
with t4:
    tab_produk()
