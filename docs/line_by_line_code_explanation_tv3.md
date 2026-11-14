# HƯỚNG DẪN GIẢI THÍCH MÃ NGUỒN TỪNG DÒNG (LINE-BY-LINE CODE WALKTHROUGH)
## PHÂN HỆ TẦNG MẠNG, GOSSIP 2 PHA & RATE LIMITER (TV3)
**Học phần**: Công nghệ Blockchain & Ứng dụng | **Khóa học**: 2026  
**Tác giả**: Thành viên 3 (TV3) — Phụ trách Tầng Mạng Mô Phỏng  
**Tài liệu tham chiếu**: `src/network/rate_limiter.py`, `src/network/gossip.py`, `src/network/simulator.py`  

---

## MỤC ĐÍCH TÀI LIỆU
Tài liệu này được biên soạn nhằm mục đích giúp bạn (TV3) có thể:
1. Nắm bắt bản chất hoạt động ở cấp độ vi mô của từng dòng lệnh.
2. Cầm tài liệu lên và **nói bằng lời tự nhiên (Verbal Explanation)** trước nhóm hoặc khi giảng viên yêu cầu giải thích chi tiết mã nguồn trên lớp.
3. Hiểu rõ các lỗi kỹ thuật phi hiển nhiên (*Non-trivial bugs*) và lý do tại sao phải viết như vậy.

---

# MỤC LỤC
1. **Phân tích chi tiết `src/network/rate_limiter.py`**
   - 1.1. Hàm `check_inbound()` (Thẩm định gói tin đến & Phát hiện DoS)
   - 1.2. Hàm `is_peer_blocked()` (Kiểm tra và tự động gỡ phạt)
   - 1.3. Hàm `unblock_expired()` (Quét và giải phóng các án phạt)
2. **Phân tích chi tiết `src/network/gossip.py`**
   - 2.1. Hàm `broadcast_header()` (Proposer phát tán thực đơn)
   - 2.2. Hàm `handle_header_packet()` (Validator thẩm định Header & gửi Body Request)
   - 2.3. Hàm `handle_body_request()` (Proposer gửi thân khối Body theo yêu cầu)
   - 2.4. Hàm `handle_body_packet()` (Validator duyệt Body & Chống khối mồ côi)
3. **Phân tích chi tiết `src/network/simulator.py`**
   - 3.1. Hàm `_enqueue_packet()` (Tính toán rớt gói & trễ theo Partial Synchrony)
   - 3.2. Hàm `_schedule_packet_delivery()` (Chèn sự kiện vào Heap & Giải thích biến `_seq`)
   - 3.3. Hàm `step()` (Trái tim vòng lặp mô phỏng rời rạc)
   - 3.4. Hàm `_dispatch_packet()` (Phân phối gói tin & Duck-Typing)
   - 3.5. Hàm `run_until()` (Điều khiển mô phỏng chạy đến mốc thời gian đích)

---

# 1. CHI TIẾT `src/network/rate_limiter.py`

### 1.1. Hàm `check_inbound()` — Kiểm tra gói tin đến có phải là spam không

```python
def check_inbound(
    self,
    peer_id: str,
    current_time: float,
    height: int = 0,
    round: int = 0,
    receiver_id: str = "",
) -> bool:
    key = f"{receiver_id}:{peer_id}" if receiver_id else peer_id
    if self.is_peer_blocked(peer_id, current_time, receiver_id=receiver_id):
        return False

    window_start = current_time - self.config.window_seconds
    timestamps = [t for t in self._peer_timestamps.get(key, []) if t >= window_start]
    timestamps.append(current_time)
    self._peer_timestamps[key] = timestamps

    if len(timestamps) > self.config.max_requests_per_window:
        unblock_at = current_time + self.config.block_duration
        self._blocked_peers[key] = unblock_at
        if self.logger is not None:
            self.logger.log(current_time, peer_id, EVENT_BLOCK, height, round, "...")
        return False

    return True
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`key = f"{receiver_id}:{peer_id}" if receiver_id else peer_id`**
   * *Ý nghĩa kỹ thuật*: Tạo khóa kép kết hợp giữa nút nhận và nút gửi (ví dụ: `"node_1:node_0"`).
   * *Lời nói tự nhiên*: *"Mình không theo dõi chung chung một nút đã gửi bao nhiêu gói ra toàn mạng, mà mình theo dõi nút đó gửi cho RIÊNG ai. Nhờ vậy, khi một Proposer broadcast 1 block hợp lệ cho 8 nút khác nhau, mỗi nút chỉ nhận 1 gói, Proposer sẽ không bị hệ thống hiểu nhầm là kẻ spam."*
2. **`if self.is_peer_blocked(peer_id, current_time, receiver_id=receiver_id): return False`**
   * *Ý nghĩa kỹ thuật*: Gọi hàm kiểm tra xem nút gửi có đang nằm trong danh sách đen và còn hiệu lực phạt không.
   * *Lời nói tự nhiên*: *"Nếu nút này đang trong thời gian thụ án phạt treo giò, hàm trả về `False` ngay lập tức để vứt bỏ gói tin ở cửa, không tốn thêm tài nguyên xử lý."*
3. **`window_start = current_time - self.config.window_seconds`**
   * *Ý nghĩa kỹ thuật*: Xác định mốc thời gian bắt đầu của cửa sổ trượt (lùi lại $1.0\text{ giây}$ so với hiện tại).
   * *Lời nói tự nhiên*: *"Mình chỉ quan tâm đến các gói tin được gửi đến trong vòng 1 giây gần đây nhất tính từ thời điểm hiện tại."*
4. **`timestamps = [t for t in self._peer_timestamps.get(key, []) if t >= window_start]`**
   * *Ý nghĩa kỹ thuật*: Dùng List Comprehension để lọc bỏ toàn bộ các mốc thời gian cũ hơn `window_start`.
   * *Lời nói tự nhiên*: *"Dòng này có nhiệm vụ dọn rác bộ nhớ: gói tin nào đến cách đây hơn 1 giây thì loại bỏ, danh sách không bị phình to vô hạn theo thời gian."*
5. **`timestamps.append(current_time)` và `self._peer_timestamps[key] = timestamps`**
   * *Ý nghĩa kỹ thuật*: Ghi nhận thời điểm của gói tin hiện tại vào danh sách và cập nhật lại từ điển.
   * *Lời nói tự nhiên*: *"Ghi nhận thêm 1 gói tin mới vừa đến tại thời điểm `current_time`."*
6. **`if len(timestamps) > self.config.max_requests_per_window:`**
   * *Ý nghĩa kỹ thuật*: Đếm tổng số gói tin trong 1 giây qua, so sánh với ngưỡng định mức (ví dụ quá 10 gói).
   * *Lời nói tự nhiên*: *"Nếu trong 1 giây vừa rồi nút này gửi dồn dập vượt quá ngưỡng định mức cho phép, mình xác định đây là hành vi spam / DoS."*
7. **`unblock_at = current_time + self.config.block_duration` và `self._blocked_peers[key] = unblock_at`**
   * *Ý nghĩa kỹ thuật*: Tính thời điểm hết hạn phạt (hiện tại + 5.0 giây) và lưu vào từ điển `_blocked_peers`.
   * *Lời nói tự nhiên*: *"Hệ thống rút thẻ đỏ: khóa nút này lại trong vòng 5 giây tiếp theo."*
8. **`self.logger.log(...)` và `return False`**
   * *Ý nghĩa kỹ thuật*: Ghi nhận sự kiện `EVENT_BLOCK` vào nhật ký tất định và từ chối gói tin vi phạm.
9. **`return True`**
   * *Ý nghĩa kỹ thuật*: Gói tin hoàn toàn hợp lệ, cho phép chuyển tiếp vào mạng.

---

### 1.2. Hàm `is_peer_blocked()` — Kiểm tra và tự động gỡ phạt

```python
def is_peer_blocked(self, peer_id: str, current_time: float, receiver_id: str = "") -> bool:
    key = f"{receiver_id}:{peer_id}" if receiver_id else peer_id
    unblock_time = self._blocked_peers.get(key)
    if unblock_time is None:
        return False

    if current_time >= unblock_time:
        del self._blocked_peers[key]
        self._peer_timestamps.pop(key, None)
        if self.logger is not None:
            self.logger.log(current_time, peer_id, EVENT_UNBLOCK, 0, 0, "...")
        return False

    return True
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`unblock_time = self._blocked_peers.get(key)`**
   * *Lời nói tự nhiên*: *"Mình tra cứu xem peer này có án phạt nào chưa thi hành xong không. Nếu trả về `None`, nghĩa là trong sạch, trả về `False` ngay."*
2. **`if current_time >= unblock_time:`**
   * *Lời nói tự nhiên*: *"Nếu đồng hồ mô phỏng đã chạy vượt qua mốc thời gian hết án phạt (ví dụ bị phạt đến giây 15, mà hiện tại đã là giây 16)..."*
3. **`del self._blocked_peers[key]` và `self._peer_timestamps.pop(key, None)`**
   * *Lời nói tự nhiên*: *"Mình xóa tên peer khỏi sổ đen và reset lại toàn bộ lịch sử gửi tin cũ để nút có cơ hội gửi lại bình thường."*
4. **`self.logger.log(..., EVENT_UNBLOCK, ...)`**
   * *Lời nói tự nhiên*: *"Bắn ra sự kiện `EVENT_UNBLOCK` để các tầng trên biết kết nối đã thông suốt trở lại."*
5. **`return True`**
   * *Lời nói tự nhiên*: *"Nếu thời gian phạt vẫn còn hiệu lực, trả về `True` khẳng định nút này vẫn đang bị cấm."*

---

# 2. CHI TIẾT `src/network/gossip.py`

### 2.1. Hàm `broadcast_header()` — Proposer phát tán thực đơn khối

```python
def broadcast_header(self, header: BlockHeader, body: BlockBody | None = None) -> bytes:
    header_hash = self._compute_header_hash(header)
    self.accepted_headers.add(header_hash)
    self.headers_by_hash[header_hash] = header
    if body is not None:
        self.stored_bodies[header_hash] = body

    payload = self._serialize_header(header)
    self.network.broadcast_header(self.node_id, payload)
    return header_hash
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`header_hash = self._compute_header_hash(header)`**: Tính toán mã băm SHA-256 định danh cho Header.
2. **`self.accepted_headers.add(header_hash)`**: Nút đề xuất tự ghi nhận Header của chính mình là hợp lệ.
3. **`if body is not None: self.stored_bodies[header_hash] = body`**: 
   * *Lời nói tự nhiên*: *"Proposer cất thân khối Body vào kho nội bộ `stored_bodies`. Tuyệt đối chưa gửi Body ra mạng lúc này!"*
4. **`self.network.broadcast_header(self.node_id, payload)`**: 
   * *Lời nói tự nhiên*: *"Chỉ tuần tự hóa và phát tán duy nhất `BlockHeader` nhẹ ra mạng để các validator thẩm định trước."*

---

### 2.2. Hàm `handle_header_packet()` — Validator thẩm định Header & gửi Body Request

```python
def handle_header_packet(self, sender: str, header: BlockHeader) -> bool:
    if self.header_validator is not None:
        if not self.header_validator(header):
            return False

    header_hash = self._compute_header_hash(header)
    self.accepted_headers.add(header_hash)
    self.headers_by_hash[header_hash] = header
    self.pending_body_requests[header_hash] = sender

    request_packet = NetPacket(MSG_BODY_REQUEST, self.node_id, sender, payload=header_hash)
    self.network.send(self.node_id, sender, self._serialize_packet(request_packet))
    return True
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`if not self.header_validator(header): return False`**: 
   * *Lời nói tự nhiên*: *"Khi nhận được tờ thực đơn Header, việc đầu tiên là kiểm tra tính hợp lệ: chữ ký của Proposer có chuẩn không, parent hash có khớp đỉnh chuỗi không. Nếu sai thì từ chối ngay."*
2. **`self.accepted_headers.add(header_hash)`**: 
   * *Lời nói tự nhiên*: *"Header hợp lệ thì đóng dấu chứng nhận bằng cách thêm vào `accepted_headers`. Đây là chiếc chìa khóa để lát nữa duyệt thân khối Body."*
3. **`request_packet = NetPacket(MSG_BODY_REQUEST, ...)` và `self.network.send(...)`**: 
   * *Lời nói tự nhiên*: *"Gửi gói tin `MSG_BODY_REQUEST` nhắn cho Proposer: 'Tôi đã duyệt Header của bạn, hãy gửi danh sách giao dịch (Body) tương ứng qua cho tôi'."*

---

### 2.3. Hàm `handle_body_packet()` — Validator duyệt Body & Chống khối mồ côi

```python
def handle_body_packet(self, sender: str, body: BlockBody) -> bool:
    if body.header_hash not in self.accepted_headers:
        return False

    self.accepted_bodies[body.header_hash] = body
    self.pending_body_requests.pop(body.header_hash, None)
    return True
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`if body.header_hash not in self.accepted_headers: return False`**: 
   * *Lời nói tự nhiên*: *"Đây chính là chốt chặn phòng ngự chống tấn công Orphan Body Flooding: Nếu ai đó ném một thân khối Body nặng vào mạng mà Header của nó chưa từng được duyệt trong `accepted_headers`, mình vứt bỏ ngay lập tức ở dòng này, không tốn tài nguyên giải mã."*
2. **`self.accepted_bodies[body.header_hash] = body`**: 
   * *Lời nói tự nhiên*: *"Nếu mã băm khớp, mình chính thức tiếp nhận thân khối và ráp lại với Header thành khối hoàn chỉnh (Full Block) chuyển cho Consensus và Ledger."*

---

# 3. CHI TIẾT `src/network/simulator.py`

### 3.1. Hàm `_enqueue_packet()` — Tính toán trễ & rớt gói theo Partial Synchrony

```python
def _enqueue_packet(self, packet: NetPacket) -> None:
    if not self.rate_limiter.check_outbound(packet.sender, self.current_time):
        self.total_dropped += 1
        return

    is_pre_gst = self.current_time < self.config.gst

    if is_pre_gst:
        if self.config.drop_rate_pre_gst > 0.0 and self.rng.random() < self.config.drop_rate_pre_gst:
            self.total_dropped += 1
            if self.logger is not None:
                self.logger.log(self.current_time, packet.sender, EVENT_DROP, 0, 0, "...")
            return

        delay = self.rng.uniform(self.config.min_delay, self.config.max_delay_pre_gst)

        if self.config.dup_rate_pre_gst > 0.0 and self.rng.random() < self.config.dup_rate_pre_gst:
            self.total_duplicates += 1
            dup_delay = delay + self.rng.uniform(0.01, 0.5)
            self._schedule_packet_delivery(packet, dup_delay)
    else:
        max_delay = max(self.config.min_delay, self.config.delta)
        delay = self.rng.uniform(self.config.min_delay, max_delay)

    self._schedule_packet_delivery(packet, delay)
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`if not self.rate_limiter.check_outbound(...): return`**: 
   * *Lời nói tự nhiên*: *"Kiểm tra quota gửi ra của node gửi. Nếu phát tin quá tải thì hủy gói tin ngay để bảo vệ buffer."*
2. **`is_pre_gst = self.current_time < self.config.gst`**: 
   * *Lời nói tự nhiên*: *"Kiểm tra xem thời gian ảo hiện tại đang ở vùng giông bão (Trước GST) hay vùng trời quang (Sau GST)."*
3. **Khối lệnh `if is_pre_gst:` (Trước GST)**:
   * `if self.rng.random() < self.config.drop_rate_pre_gst: return`
     * *Lời nói tự nhiên*: *"Gieo xúc xắc ngẫu nhiên độc lập: nếu rơi vào tỷ lệ rớt (ví dụ 35%), gói tin bị triệt tiêu khỏi mạng, không đưa vào heap."*
   * `delay = self.rng.uniform(self.config.min_delay, self.config.max_delay_pre_gst)`
     * *Lời nói tự nhiên*: *"Trước GST, độ trễ bị dao động rất lớn, có thể lên đến 5 hoặc 8 giây."*
   * `if ... < dup_rate_pre_gst: self._schedule_packet_delivery(packet, dup_delay)`
     * *Lời nói tự nhiên*: *"Mô phỏng nhân bản gói tin: chèn thêm một bản sao với độ trễ lệch nhau."*
4. **Khối lệnh `else:` (Sau GST)**:
   * `delay = self.rng.uniform(self.config.min_delay, max_delay)`
     * *Lời nói tự nhiên*: *"Sau GST, tỷ lệ rớt gói bằng 0, và độ trễ được chặn trên nghiêm ngặt bởi $\Delta = 2.0\text{ giây}$. Đảm bảo gói tin luôn đến đích đúng hạn."*
5. **`self._schedule_packet_delivery(packet, delay)`**: Đẩy gói tin vào hàng đợi ưu tiên.

---

### 3.2. Hàm `_schedule_packet_delivery()` — Chèn sự kiện vào Heap & Giải thích biến `_seq`

```python
def _schedule_packet_delivery(self, packet: NetPacket, delay: float) -> None:
    delivery_time = self.current_time + delay
    self._seq += 1

    heapq.heappush(
        self._queue,
        (delivery_time, self._seq, "PACKET", packet),
    )
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh (CÂU HỎI VẤN ĐÁP CỐT LÕI):
1. **`delivery_time = self.current_time + delay`**: Tính toán thời điểm phân phát gói tin trong tương lai.
2. **`self._seq += 1`**: Tăng biến đếm đơn điệu lên 1 đơn vị.
3. **`heapq.heappush(self._queue, (delivery_time, self._seq, "PACKET", packet))`**:
   * *Câu trả lời chuẩn khi thi vấn đáp*:  
     > *"Tại sao tuple lưu trong `heapq` bắt buộc phải có 4 phần tử với `self._seq` nằm ở vị trí thứ hai?  
     Bởi vì trong thuật toán hàng đợi ưu tiên của Python, nếu hai gói tin có cùng thời gian giao hàng `delivery_time`, Python sẽ so sánh phần tử kế tiếp.  
     Nếu không có `_seq`, Python sẽ cố so sánh trực tiếp hai đối tượng `NetPacket`, gây ra lỗi văng chương trình `TypeError: '<' not supported between instances of 'NetPacket'`.  
     Nhờ có biến `self._seq` luôn tăng ngặt ($1, 2, 3\dots$), giá trị này luôn duy nhất, giúp `heapq` không bao giờ phải so sánh trường `packet`, đồng thời bảo đảm gói tin nào gửi trước sẽ được xử lý trước (**FIFO tất định 100%**)."*

---

### 3.3. Hàm `step()` — Trái tim vòng lặp mô phỏng rời rạc

```python
def step(self) -> bool:
    if not self._queue:
        return False

    delivery_time, _, event_type, data = heapq.heappop(self._queue)
    self.current_time = delivery_time

    if self.rate_limiter is not None:
        self.rate_limiter.unblock_expired(self.current_time)

    if event_type == "PACKET":
        packet: NetPacket = data
        self._dispatch_packet(packet)
    elif event_type == "TIMEOUT":
        node_id, callback_tag = data
        self._dispatch_timeout(node_id, callback_tag)

    return True
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`if not self._queue: return False`**: Nếu hàng đợi cạn kiệt sự kiện, kết thúc mô phỏng.
2. **`delivery_time, _, event_type, data = heapq.heappop(self._queue)`**: Rút sự kiện có mốc thời gian sớm nhất ra khỏi đỉnh heap với độ phức tạp tối ưu $\mathcal{O}(\log N)$.
3. **`self.current_time = delivery_time`**: 
   * *Lời nói tự nhiên*: *"Đồng hồ mô phỏng lập tức nhảy cóc tới thời điểm của sự kiện mà không tốn bất kỳ giây chờ đợi nào của máy tính thực tế."*
4. **`self.rate_limiter.unblock_expired(self.current_time)`**: Tự động giải phóng các án phạt spam đã hết hạn tính đến thời điểm này.
5. **`if event_type == "PACKET": self._dispatch_packet(packet)`**: Nếu là gói tin, chuyển tiếp cho nút nhận.
6. **`elif event_type == "TIMEOUT": self._dispatch_timeout(node_id, callback_tag)`**: Nếu là bộ hẹn giờ, bóc tách tag và gọi hàm xử lý timeout của validator để chuyển round đồng thuận.
7. **`return True`**: Báo hiệu xử lý thành công 1 sự kiện.

---

### 3.4. Hàm `_dispatch_packet()` — Phân phối gói tin & Duck-Typing

```python
def _dispatch_packet(self, packet: NetPacket) -> None:
    if self.rate_limiter is not None:
        if not self.rate_limiter.check_inbound(
            packet.sender,
            self.current_time,
            receiver_id=packet.receiver,
        ):
            self.total_dropped += 1
            return

    receiver_node = self._nodes.get(packet.receiver)
    if receiver_node is None:
        return

    if hasattr(receiver_node, "handle_packet"):
        receiver_node.handle_packet(self.current_time, packet.payload)
    elif hasattr(receiver_node, "on_packet"):
        receiver_node.on_packet(self.current_time, packet.payload)
    elif callable(receiver_node):
        receiver_node(self.current_time, packet)
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`if not self.rate_limiter.check_inbound(...): return`**: 
   * *Lời nói tự nhiên*: *"Trước khi giao gói tin, kiểm tra xem nút nhận có đang chặn nút gửi hay không. Nếu người gửi đang bị chặn spam thì gói tin bị hủy ngay tại đích."*
2. **`receiver_node = self._nodes.get(packet.receiver)`**: Tra cứu đối tượng nút nhận từ danh bạ.
3. **Khối lệnh `if hasattr(...):` (Kỹ thuật Duck-Typing)**:
   * *Lời nói tự nhiên*: *"Để module mạng của mình có thể tương thích với bất kỳ code nào của TV4 hay các mock test, mình dùng Duck-Typing: Nếu node có hàm `handle_packet` thì gọi `handle_packet`, nếu có `on_packet` thì gọi `on_packet`, nếu là hàm callback thì gọi trực tiếp."*

---

### 3.5. Hàm `run_until()` — Điều khiển mô phỏng chạy đến mốc thời gian đích

```python
def run_until(self, max_time: float | int) -> int:
    target_time = float(max_time)
    events_processed = 0
    while self._queue:
        next_time = self._queue[0][0]
        if next_time > target_time:
            break
        self.step()
        events_processed += 1

    if self.current_time < target_time:
        self.current_time = target_time
        if self.rate_limiter is not None:
            self.rate_limiter.unblock_expired(self.current_time)

    return events_processed
```

#### 🎙️ Giải thích bằng lời từng dòng lệnh:
1. **`next_time = self._queue[0][0]` và `if next_time > target_time: break`**: Nhìn trước thời điểm của sự kiện kế tiếp trong heap; nếu đã vượt qua mốc thời gian yêu cầu thì dừng vòng lặp.
2. **`self.step()` và `events_processed += 1`**: Lần lượt xử lý từng sự kiện và đếm số sự kiện hoàn thành.
3. **`if self.current_time < target_time: self.current_time = target_time`**:
   * *Câu trả lời khi giải thích lỗi Non-trivial Bug số 2*:  
     > *"Nếu tất cả sự kiện trước mốc 25.0s đã xử lý xong từ giây thứ 7.43s và hàng đợi cạn, nếu không có dòng này, đồng hồ sẽ bị đứng yên ở 7.43s. Dòng code này giúp ép đồng hồ nhảy thẳng tới đúng mốc 25.0s, bảo đảm khi bước sang pha sau, mạng nhận diện chính xác là đã qua mốc GST."*
4. **`self.rate_limiter.unblock_expired(self.current_time)`**: Giải phóng các án phạt hết hạn tính đến mốc `target_time`.
5. **`return events_processed`**: Trả về tổng số sự kiện đã thực thi.

---

## 4. TỔNG KẾT BỎ TÚI KHI BẢO VỆ ĐỒ ÁN
* **Khóa kép Rate Limiter**: `f"{receiver_id}:{peer_id}"` giúp tránh phạt nhầm khi broadcast.
* **Gossip 2 pha**: Chỉ gửi `BlockHeader` trước; từ chối mọi `BlockBody` nếu không có Header trong `accepted_headers`.
* **Biến `_seq` trong heapq**: Giải quyết triệt để lỗi so sánh kiểu dữ liệu `TypeError` và bảo toàn thứ tự FIFO tất định.
* **Đồng hồ ảo trong `run_until`**: Ép đồng hồ nhảy tới `target_time` để tránh lỗi đóng băng thời gian trước GST.
