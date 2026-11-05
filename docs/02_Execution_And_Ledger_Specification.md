# BÁO CÁO KỸ THUẬT: TẦNG EXECUTION ENGINE VÀ LEDGER (THÀNH VIÊN 2)
## ĐỒ ÁN LAB 01: XÂY DỰNG MINIMAL LAYER 1 BLOCKCHAIN & BFT CONSENSUS SIMULATOR
### MÔN HỌC: BLOCKCHAIN VÀ ỨNG DỤNG — HCMUS

- **Thành viên phụ trách:** Trương Thái Nguyên (MSSV: 23120308) — Thành viên 2 (TV2)
- **Phạm vi kỹ thuật:** Tầng thực thi giao dịch (Execution Layer), Quản lý trạng thái (State Engine), Bảng băm Merkle (State Commitment) và Sổ cái cục bộ (Ledger View).
- **Mã nguồn triển khai:** `src/execution/state.py`, `src/execution/commitment.py`, `src/execution/ledger.py`
- **Bộ kiểm thử tự động:** `run_tv2_tests.py`, `tests/unit/test_state_ledger.py`, `tests/e2e/test_t1_t2_simulation.py`

---

## 1. Kiến trúc Tổng quan Tầng Execution & Ledger

Tầng Execution đóng vai trò là hạt nhân xử lý logic nghiệp vụ và lưu trữ dữ liệu của mỗi node trong mạng blockchain. Kiến trúc được thiết kế theo mô hình máy trạng thái đơn định (Deterministic State Machine), tách biệt rõ ràng giữa giai đoạn thẩm định giả định (dry-run / isolated execution) và giai đoạn ghi sổ vĩnh viễn (commit).

```
          Incoming Proposed Block (from Consensus Engine)
                               │
                               ▼
        ┌─────────────────────────────────────────────────────────────┐
        │                  Ledger (Sổ cái cục bộ)                     │
        │  1. Kiểm tra parent_hash khớp với đỉnh chuỗi hiện tại      │
        │  2. Kiểm tra block_height = current_height + 1              │
        └──────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
        ┌─────────────────────────────────────────────────────────────┐
        │            StateEngine (Thực thi giao dịch)                 │
        │  1. Kiểm tra quyền sở hữu khóa: 'Sender/' prefix check      │
        │  2. Kiểm tra Replay Attack: Nonce & Tx Hash tracking        │
        │  3. Áp dụng biến đổi trạng thái trong bộ nhớ tạm (Dry-run)  │
        └──────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
        ┌─────────────────────────────────────────────────────────────┐
        │           StateCommitment (Merkle Tree Root)                │
        │  1. Sắp xếp từ điển các key-value pairs (Canonical sort)   │
        │  2. Tính toán SHA-256 Merkle / Trie State Hash              │
        │  3. Đối chiếu: state_hash_computed == header.state_hash     │
        └──────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
           Match? ──[YES]──► Commit Block & Update Ledger View
                  ──[NO]───► Reject Block (Invalid State Root)
```

---

## 2. Chi tiết Thiết kế & Hiện thực Kỹ thuật

### 2.1. Quản lý Trạng thái & Cơ chế Chống Tấn công (StateEngine)
Tệp `src/execution/state.py` hiện thực lớp `StateEngine` tuân thủ giao thức `StateProtocol`:
1. **Kiểm soát quyền sở hữu khóa (Key Ownership Rule):**
   - Người gửi chỉ có quyền ghi/sửa/xóa các khóa bắt đầu bằng tiền tố `<Sender_ID>/` (ví dụ: Alice chỉ được ghi khóa `Alice/balance`, không thể can thiệp vào `Bob/balance`).
   - Mọi nỗ lực can thiệp khóa ngoài phạm vi sở hữu sẽ lập tức ném ngoại lệ `PermissionError` và loại bỏ giao dịch.
2. **Cơ chế Chống Phát lại (Replay Protection):**
   - Theo dõi bộ băm giao dịch đã thực thi (`executed_tx_hashes: set[str]`) và bảng ánh xạ số thứ tự nonces (`account_nonces: dict[str, int]`).
   - Giao dịch có cùng `tx_id` hoặc nonce không tăng tiến đơn điệu đều bị từ chối với ngoại lệ `ReplayTransactionError`.
3. **Thực thi Đơn định & Cô lập Bộ nhớ (Dry-run Isolation):**
   - Phương thức `validate_and_compute_hash(txs)` thực hiện sao chép trạng thái (snapshot copy), áp dụng danh sách giao dịch trên bản sao để tính `state_hash` mà không làm thay đổi trạng thái cam kết hiện tại nếu block chưa đạt được sự đồng thuận 2/3+ (PoLC / Quorum).

### 2.2. Tính toán State Commitment (Merkle Tree Hash)
Tệp `src/execution/commitment.py` hiện thực thuật toán băm cam kết trạng thái:
- Mọi cặp khóa-giá trị trong trạng thái được chuẩn hóa qua thuật toán Canonical Serialization (sắp xếp khóa theo thứ tự từ điển UTF-8 để triệt tiêu tính bất định).
- Tính toán lá Merkle bằng cách kết hợp `SHA-256(canonical_key || ":" || canonical_value)`.
- Hai node độc lập nhận cùng danh sách giao dịch hợp lệ đảm bảo sinh ra cùng một chuỗi byte `state_hash` với xác suất xung đột xấp xỉ bằng $0$.

### 2.3. Quản lý Sổ cái Cục bộ (Ledger)
Tệp `src/execution/ledger.py` hiện thực lớp `Ledger` tuân thủ `LedgerProtocol`:
- Quản lý danh sách các khối đã được đồng thuận `finalized_chain: list[Block]`.
- Khởi tạo khối nguyên thủy Genesis Block tại chiều cao `height = 0` với `parent_hash = "0"*64`.
- Quản lý trạng thái pending và finalized. Cung cấp API `commit_block(block)` chỉ ghi nhận khối khi cả `parent_hash` và `state_hash` đều khớp với kết quả thẩm định từ `StateEngine`.

---

## 3. Kết quả Kiểm thử & Đo kiểm Thực nghiệm

Toàn bộ mã nguồn TV2 được kiểm thử độc lập thông qua script `run_tv2_tests.py`. Kết quả chạy thực tế:

```text
Ran 12 tests in 0.003s
OK (100% SUCCESS RATE)
```

| Mã kiểm thử | Tên bài kiểm tra | Nội dung kiểm chứng | Kết quả |
|---|---|---|:---:|
| **UT-01** | `test_protocol_compliance` | Kiểm tra tính tương thích giao diện `StateProtocol` & `LedgerProtocol`. | **PASSED** |
| **UT-02** | `test_ownership_validation` | Xác minh Alice không thể sửa đổi khóa của Bob (`Bob/data`). | **PASSED** |
| **UT-03** | `test_replay_protection` | Gửi lại giao dịch trùng `tx_id` hoặc nonce cũ bị từ chối ngay lập tức. | **PASSED** |
| **UT-04** | `test_dry_run_isolation` | Tính toán `state_hash` giả định không làm thay đổi trạng thái gốc. | **PASSED** |
| **UT-05** | `test_deterministic_state_hash` | Hai instance `StateEngine` độc lập cùng nhận tập txs sinh ra cùng một hash. | **PASSED** |
| **UT-06** | `test_genesis_block` | Khởi tạo khối Genesis tại độ cao 0 đúng chuẩn. | **PASSED** |
| **UT-07** | `test_invalid_parent_hash` | Khối có parent hash sai lệch bị Sổ cái từ chối. | **PASSED** |
| **UT-08** | `test_invalid_state_hash` | Khối chứa `state_hash` giả mạo bị Sổ cái phát hiện và bác bỏ. | **PASSED** |
| **UT-09** | `test_valid_block_accepted` | Khối hợp lệ được commit thành công và cập nhật `state_root`. | **PASSED** |
| **E2E-T1** | `test_t1_state_convergence` | Mô phỏng 8 nodes mạng nhận tập giao dịch phân tán, đạt trạng thái hội tụ 100%. | **PASSED** |
| **E2E-T2** | `test_t2_duplicate_reordered` | Xử lý mạng chập chờn: Gói tin trùng lặp hoặc đảo thứ tự không bị thực thi 2 lần. | **PASSED** |
| **E2E-T3** | `test_protocol_contract` | Kiểm tra toàn vẹn kiểu dữ liệu đầu ra và khả năng cắm nối với Consensus Engine. | **PASSED** |

---

## 4. Giao diện Đấu nối với TV1 (Crypto) & TV3 (Consensus/Network)

Tầng Execution của TV2 đã sẵn sàng 100% để tích hợp hệ thống:
1. **Đầu vào từ TV1 (Crypto):** Nhận đối tượng `Transaction` chứa chữ ký Ed25519 đã xác thực và hàm băm Canonical.
2. **Đầu vào từ TV3/TV4 (Consensus Node):** Khi Consensus đề xuất `Block`, gọi `ledger.validate_block(block, state_engine)`.
3. **Đầu ra ghi sổ:** Trả về mã lỗi hoặc xác nhận `commit_block()`, cung cấp `state_hash` cho khối tiếp theo.
