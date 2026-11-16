# Báo cáo các lỗi phi hiển nhiên và giải pháp kiến trúc
## Lab 01: Minimal Layer 1 Blockchain and BFT Consensus Simulator
### Khoa Công nghệ Thông tin, Trường Đại học Khoa học Tự nhiên, ĐHQG-HCM

## 1. Phân hệ Thực thi và Sổ cái (TV2)

### BUG-TV2-01: Giao dịch lỗi làm biến đổi trạng thái gốc khi khối bị từ chối
- Thành phần: `src/execution/state.py:27-33`
- Nguyên nhân: Các node validator thực thi giao dịch trực tiếp lên từ điển trạng thái chính `self._data`. Khi giao dịch thứ $i$ trong một khối gồm $k$ giao dịch vi phạm quyền sở hữu khóa hoặc sai thứ tự nonce, các thay đổi từ giao dịch $1$ đến $i-1$ vẫn lưu lại trong bộ nhớ. Node bị sai lệch trạng thái dù thuật toán đồng thuận đã từ chối khối này.
- Cách xử lý: Tách biệt giai đoạn chạy thử bằng phương thức `clone()`. Hàm này sao chép độc lập `_data`, `_nonces`, và `_executed_tx_hashes`. Quá trình kiểm tra khối gọi `validate_and_compute_hash()` trên bản sao. Nếu phát hiện giao dịch không hợp lệ, hệ thống hủy bản sao, giữ nguyên trạng thái gốc.

### BUG-TV2-02: Trùng mã băm do dùng dấu hai chấm phân tách trường
- Thành phần: `src/execution/commitment.py:22,75`
- Nguyên nhân: Hàm `compute_tx_hash` và `compute_state_commitment` ghép các trường thành chuỗi qua dấu hai chấm: `f"{tx.sender}:{tx.key}:{tx.value}:{tx.nonce}"` và `f"{k}:{v}"`. Khóa hoặc giá trị chứa sẵn ký tự hai chấm sẽ sinh ra chuỗi byte trùng nhau. Ví dụ: khóa `Alice/a:b` với giá trị `c` tạo ra chuỗi `Alice/a:b:c`, trùng hoàn toàn với khóa `Alice/a` mang giá trị `b:c`.
- Cách xử lý: Bổ sung tiền tố ghi nhận độ dài từng chuỗi trước khi ghép để phân định ranh giới dữ liệu trước khi băm SHA-256.

### BUG-TV2-03: Trùng Merkle root khi số lượng phần tử lẻ
- Thành phần: `src/execution/commitment.py:49-51`
- Nguyên nhân: Khi một tầng trong cây Merkle có số phần tử lẻ, thuật toán nhân đôi phần tử cuối bằng `current_level.append(current_level[-1])`. Do đó, khối chứa ba giao dịch $[T_1, T_2, T_3]$ sinh ra Merkle root giống hệt khối chứa bốn giao dịch $[T_1, T_2, T_3, T_3]$, lặp lại lỗ hổng CVE-2012-2459.
- Cách xử lý: Thêm byte phân loại `0x00` cho lá và `0x01` cho nút nhánh trước khi băm để tránh trùng lặp giữa các cấu trúc cây khác nhau.

## 2. Phân hệ Mạng mô phỏng (TV3)

### BUG-TV3-01: Bộ giới hạn tốc độ nhận diện nhầm hành vi phát sóng khối
- Thành phần: `src/network/rate_limiter.py:68`
- Nguyên nhân: Bộ giới hạn tốc độ theo dõi lưu lượng bằng khóa đơn `sender_id`. Khi đề xuất khối trong cụm từ 8 node trở lên, người gửi phát gói tin đến tất cả các node trong cùng một mốc thời gian mô phỏng. Hệ thống nhận diện đợt gửi này là tấn công từ chối dịch vụ và kích hoạt `EVENT_BLOCK`, làm rớt gói tin gửi đến các node có chỉ số cao.
- Cách xử lý: Chuyển sang theo dõi bằng khóa kép `key = f"{receiver_id}:{peer_id}"`. Ngưỡng kiểm tra áp dụng riêng cho từng kênh liên lạc, cho phép phát sóng đồng thời đến nhiều node mà không kích hoạt án phạt nhầm.

### BUG-TV3-02: Hàng đợi ưu tiên dừng đột ngột khi trùng mốc thời gian
- Thành phần: `src/network/simulator.py:130-134`
- Nguyên nhân: Khi nhiều gói tin có cùng thời điểm chuyển phát $t_1 = t_2$, thư viện `heapq` so sánh phần tử kế tiếp trong tuple. Do phần tử này là đối tượng `NetPacket` không định nghĩa toán tử `<`, Python dừng chương trình với lỗi `TypeError`.
- Cách xử lý: Đưa biến đếm nguyên tự tăng ngặt `_seq` vào tuple: `(delivery_time, _seq, event_type, data)`. Phép so sánh luôn phân định tại `_seq`, không chạm vào đối tượng dữ liệu và giữ đúng thứ tự đến trước xử lý trước.

## 3. Phân hệ Máy trạng thái Đồng thuận (TV4)

### BUG-TV4-01: Khuếch đại lưu lượng khi phản hồi yêu cầu thân khối
- Thành phần: `src/consensus/fsm.py:728-736`
- Nguyên nhân: Giao thức mạng gửi header trước body. Khi nhận header mà chưa có body, node phát yêu cầu `MSG_BODY_REQUEST`. Hàm xử lý `on_packet()` bóc tách dữ liệu mà không lưu định danh node gửi. Khi phản hồi, node lặp qua danh sách `self.validators` và phát sóng thân khối cho tất cả các node thay vì chỉ gửi riêng cho node cần.
- Cách xử lý: Cập nhật hàm tiếp nhận gói tin để giữ lại thông tin người gửi, chuyển phản hồi `MSG_BODY` thành tin nhắn gửi đích danh.

## 4. Phân hệ An toàn và Chống gian lận Byzantine (TV5)

### BUG-TV5-01: Sai lệch túc số khi nhận các phiếu bầu mâu thuẫn
- Thành phần: `src/security/locking.py:180-195`
- Nguyên nhân: Nút Byzantine ký hai phiếu mâu thuẫn cho hai khối khác nhau trong cùng một lượt. Nếu bộ lưu trữ ghi đè phiếu cũ bằng phiếu mới nhận, độ trễ mạng ngẫu nhiên sẽ khiến các node trung thực ghi nhận các túc số khác nhau, dẫn đến chia nhánh chuỗi.
- Cách xử lý: Giữ nguyên phiếu hợp lệ đầu tiên được ghi nhận. Phiếu mâu thuẫn đến sau chỉ được lưu vào danh sách bằng chứng gian lận `EquivocationEvidence` để phục vụ đối soát, không tính vào túc số biểu quyết.
