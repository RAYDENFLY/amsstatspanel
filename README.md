# AMSSTUDIO PANEL

Ringkasan singkat untuk menjalankan dan menyesuaikan panel ini.

- Backend API: [server.py](server.py)  
  - Endpoint utama: `/api/stats` (lihat [`collect`](server.py) yang mengumpulkan semua data).
  - Konfigurasi penting:
    - Port: [`PORT`](server.py)
    - Interface jaringan untuk statistik: [`IFACE`](server.py)
    - Mount disk yang dihitung: [`DISK_MOUNT`](server.py)

- Frontend: [index.html](index.html)  
  - Panel mengambil data dari API yang diatur pada objek [`CONFIG`](index.html).
  - Jika API tidak tersedia, panel bisa menampilkan data fallback dari fungsi [`getDemoData`](index.html).

Persiapan & menjalankan
1. (Opsional) Sesuaikan nilai [`IFACE`](server.py), [`DISK_MOUNT`](server.py) atau [`PORT`](server.py) jika diperlukan.
2. Jalankan server:
```sh
python3 [server.py](http://_vscodecontentref_/0)

RAYDENFLY