# BÁO CÁO GIẢI QUYẾT DUPLICATE HEADER-BEFORE-BODY & ĐỒNG BỘ KIỂM CHỨNG TÍCH HỢP

## 1. Thông tin Tổng quan
- **Repository:** `https://github.com/MaTuyetNganHCMUS/blockchain-lab01`
- **Branch:** `fix/resolve-duplicate-header-body-implementation`
- **Base Branch:** `main`
- **Commit SHA:** `9dcacb860e969555b78b158156b8ac9489aa288f`
- **Pull Request:** [PR #10](https://github.com/MaTuyetNganHCMUS/blockchain-lab01/pull/10)
- **PR Title:** `fix(network,consensus): remove unused GossipManager and align verification with integrated FSM`

---

## A. Root Cause (Nguyên nhân Gốc rễ)

### 1. Vì sao xuất hiện hai implementation?
- **Giai đoạn TV3 (PR #5):** TV3 hiện thực độc lập module `GossipManager` tại `src/network/gossip.py` nhằm thỏa mãn yêu cầu Mục 6 của đề bài (giao thức lan truyền 2 pha Header-before-Body). Module này được xây dựng độc lập với máy trạng thái đồng thuận, có bộ nhớ cache cục bộ riêng (`accepted_headers`, `stored_bodies`, `accepted_bodies`) và unit tests độc lập (`tests/unit/test_network.py`, `scripts/verify_tv3.py`).
- **Giai đoạn TV4 Integration (PR #6 & PR #9):** Khi tích hợp toàn diện 5 phân hệ vào `ValidatorNode` và `simulation.py`, phân hệ đồng thuận TV4 đã nối trực tiếp `NetworkSimulator` vào `ConsensusFSM` (`src/consensus/fsm.py`). Toàn bộ luồng kéo thân khối 2 pha (`_propose_block()`, `_request_block_body()`, `on_proposal_header()`, `on_proposal_body()`) và thuật toán Transaction Bounded Gossip (`admit_transaction()`, `_gossip_transaction()`) được cài đặt trực tiếp bên trong `ConsensusFSM` để điều phối vòng đời đồng thuận và quản lý transition state chặt chẽ.

### 2. Vì sao `GossipManager` không tham gia simulation?
- `ValidatorNode.create_integrated(...)` khởi tạo `ConsensusFSM` và đăng ký node trực tiếp với `NetworkSimulator`.
- Không có bất kỳ entrypoint sản xuất nào (`src/__main__.py`, `src/simulation.py`, `src/consensus/node.py`, `src/consensus/fsm.py`) import hoặc khởi tạo `GossipManager`.
- Khi các validator giao tiếp qua mạng:
  - Inbound packet từ `NetworkSimulator` đi thẳng vào `ValidatorNode.handle_packet()`, chuyển tiếp sang `ConsensusFSM.on_packet()`.
  - Outbound packet được gửi trực tiếp qua `NetworkSimulator.send()` hoặc `NetworkSimulator.broadcast_header()`.
- Do đó, `GossipManager` hoàn toàn là dead code trong simulation thực tế và chỉ tồn tại để phục vụ script đối soát TV3 và unit test cũ.

---

## B. Changes Made (Các Thay đổi đã Thực hiện)

| File | Thay đổi | Lý do |
|---|---|---|
| `src/network/gossip.py` | **Đã xóa file (delete)** | Loại bỏ implementation trùng lặp không được simulation sử dụng. |
| `src/network/__init__.py` | Loại bỏ import `GossipManager` và xóa khỏi `__all__` | Ngăn chặn broken imports và giữ export sạch sẽ (`NetworkSimulator`, `NetworkConfig`, `RateLimiter`, `RateLimiterConfig`). |
| `tests/e2e/test_t5_partial_synchrony.py` | Xóa import `GossipManager`, thay bằng test cache helper nội bộ `_TestBlockCache` | Đảm bảo test standalone T5 không bị broken import mà vẫn giữ nguyên 100% logic kiểm thử của mock node. |
| `tests/unit/test_network.py` | Thay thế các test cũ sử dụng `GossipManager` bằng 6 test cases mới kiểm chứng trực tiếp `ConsensusFSM` & `ValidatorNode` | Kiểm tra đúng implementation đang vận hành, bao quát đầy đủ 6 trường hợp bắt buộc (Reject orphan body, 2-phase ordering trace, Invalid header, Mismatched body, Body retry recovery, Non-proposer TX gossip). |
| `scripts/verify_tv3.py` | Cập nhật Section 3 sang kiểm chứng Header-before-Body trên cụm tích hợp `ValidatorNode` / `ConsensusFSM` | Giữ nguyên format và cả 5 nhóm kiểm thử của TV3, loại bỏ hoàn toàn `GossipManager`, phản ánh đúng runtime thực tế. |
| `docs/DOC-03_network_report.md` | Bổ sung bảng phân định chiến lược truyền tin, làm rõ ranh giới kiến trúc thực thi của TV3 và TV4, cập nhật số liệu test | Đảm bảo báo cáo kỹ thuật phản ánh trung thực kiến trúc hệ thống hiện hành. |
| `docs/00_System_Architecture_And_Diagrams.md` | Bổ sung bảng chiến lược truyền tin (Bounded Gossip, Broadcast, Req/Resp, Direct Fanout) và chuẩn hóa nhãn sơ đồ | Nhất quán tài liệu kiến trúc toàn diện của dự án. |
| `docs/TV5_HANDOVER_AND_REVIEW_KHANH.md` | Xóa tham chiếu file `src/network/gossip.py` trong bảng phân công phân hệ | Đồng bộ tài liệu bàn giao. |
| `README.md` | Cập nhật mô tả thư mục `src/network/` thành `packet delivery & rate limiting` | Chính xác hóa cấu trúc project. |
| `src/security/determinism.py` | Tạo mới chứa hàm `verify_determinism` chuyển từ `scripts/` vào | Đảm bảo test suite không phụ thuộc folder `scripts/` khi nộp bài. |
| `src/security/__init__.py` | Export `verify_determinism` | Công khai hàm xác thực tính tất định qua package `src.security`. |
| `tests/e2e/test_tv5_byzantine_determinism.py` | Đổi import `verify_determinism` từ `scripts.` sang `src.security.determinism` | Triệt tiêu hoàn toàn lỗi `ModuleNotFoundError: No module named 'scripts'` khi chạy test trên thư mục nộp bài. |
| `scripts/verify_tv5_determinism.py` | Import `verify_determinism` từ `src.security.determinism` | Giữ nguyên CLI runner độc lập mà tái sử dụng logic từ core module. |

---

## C. Verification Results (Kết quả Kiểm chứng Thực nghiệm)

| Nhóm Kiểm thử / Lệnh | Trạng thái | Chi tiết Kết quả |
|---|:---:|---|
| `.venv/bin/python -m pytest -q` | **PASS** | **126 passed** trong 3.5s (0 failures, 0 regressions). |
| `.venv/bin/python -m scripts.verify_tv3` | **PASS** | **5/5 nhóm PASS**: Giao diện NetworkProtocol, Rate Limiter/Peer Blocker, Header-before-Body FSM, Partial Synchrony, Seed Determinism. |
| `.venv/bin/python -m src --scenario all --config config/default.json` | **PASS** | **8/8 kịch bản PASS**: `normal` (T1), `duplicate_reorder` (T2), `invalid_signature` (T3), `replay` (T4), `partial_synchrony` (T5), `silent_proposer` (T6), `byzantine_equivocation` (T7), `determinism` (T8). |
| TV3 Unit Tests (`tests/unit/test_network.py`) | **PASS** | **13 passed** trong 0.10s (Rate Limiter, Outbound Limit, Simulator protocol, Determinism, Timeouts, Partial Synchrony boundary, và 6 Header-before-Body cases). |
| TV4 Consensus FSM (`tests/unit/test_consensus_fsm.py`) | **PASS** | **14 passed** trong 0.15s (Toàn bộ logic đồng thuận 2 pha, voting, proposal handling). |
| TV5 Security & Locking (`tests/unit/test_locking_and_votes.py`) | **PASS** | **15 passed** trong 0.08s (7 quy tắc khóa Tendermint, PoLC, equivocation detection). |
| Integrated Pipeline T1–T8 (`tests/e2e/test_full_pipeline_t1_t8.py`) | **PASS** | **8 passed** trong 3.4s (Cụm 10 node thực, Header-before-Body pull ordering, TX Gossip). |
| Deterministic Replay Verification | **PASS** | Kịch bản T8 chạy 2 lần độc lập cùng seed 42 sinh ra log hash và final state hash giống nhau tuyệt đối từng byte (`baa205980d171a3ac58ee980e2d5f2dbad9d580c775e5cbc4bb82ac02017a76e`). |

---

## D. Behavior Impact (Đánh giá Tác động Hành vi)

Xác nhận từng chức năng nghiệp vụ của hệ thống:
- **Transaction Gossip:** Giữ nguyên 100%. Giao dịch nạp vào validator ngoài proposer được kiểm tra mempool, sau đó lan truyền bằng bounded gossip với fanout mặc định là 4 (`ConsensusFSM._gossip_transaction()`). Không có thay đổi thuật toán.
- **Header Broadcast:** Giữ nguyên 100%. Proposer ký Header và phát tán qua `NetworkSimulator.broadcast_header()`.
- **Body Request/Response:** Giữ nguyên 100%. Validator nhận Header, thẩm định xong gửi `MSG_BODY_REQUEST` để kéo `BlockBody` từ Proposer.
- **Consensus & Finality:** Giữ nguyên 100%. Toàn bộ 7 quy tắc khóa, thu thập Quorum, bỏ phiếu Prevote/Precommit và Commit block đều vận hành trơn tru.
- **Network Simulation & Rate Limiting:** Giữ nguyên 100%. Quá trình điều phối hàng đợi sự kiện rời rạc bằng `heapq` với khóa đơn điệu `_seq`, mô hình bán đồng bộ GST, drop/jitter và bộ Rate Limiter token window không bị biến đổi.
- **Determinism:** Giữ nguyên 100%. Khả năng tái lập chuỗi log tất định và đồng nhất trạng thái giữa các lần chạy với cùng seed được bảo toàn.

---

## E. Pull Request

- **Branch:** `fix/resolve-duplicate-header-body-implementation`
- **Commit SHA:** `9dcacb860e969555b78b158156b8ac9489aa288f`
- **PR URL:** https://github.com/MaTuyetNganHCMUS/blockchain-lab01/pull/10

---

## F. Follow-up Findings (Các Vấn đề Nằm ngoài Phạm vi PR này)

Theo đúng chỉ đạo tại Mục 1.2 và Mục 11 của đề bài, các vấn đề sau đây là độc lập và tuyệt đối không đưa vào PR này:
- **G04:** Future Header/Vote Buffer Validation (xử lý gói tin tương lai).
- **G05:** TX Gossip Fanout Boundary (xử lý trường hợp validator count nhỏ hơn fanout).
- **G06:** TX Anti-Entropy.
- **G07:** Body Retry Liveness.
- **G08:** Body Request Bandwidth Limits.
- **G09:** Byte-level Network Throughput.
- **G10:** Packet Size Guard.
- **G11:** Packet Height Logging.
Các vấn đề này đã được ghi nhận trong backlog kỹ thuật của nhóm để xử lý ở các PR chuyên biệt tiếp theo.
