# TÀI LIỆU THIẾT KẾ HỆ THỐNG & TỔNG HỢP SƠ ĐỒ KỸ THUẬT (SYSTEM DESIGN)
## DỰ ÁN: MINIMAL LAYER 1 BLOCKCHAIN & BFT CONSENSUS SIMULATOR
### Học phần: Công nghệ Blockchain và Ứng dụng — HCMUS (Khóa 2026)
### Căn cứ đặc tả: Đề bài chính thức Lab 01 (`Lab01.pdf`)

---

## MỤC LỤC
1. [Tổng quan Kiến trúc Nút mạng Toàn diện (Node System Architecture)](#1-tổng-quan-kiến-trúc-nút-mạng-toàn-diện-node-system-architecture)
2. [Cấu trúc Dữ liệu, Mật mã & Domain Separation (Data Models & Cryptography Layer)](#2-cấu-trúc-dữ-liệu-mật-mã--domain-separation-data-models--cryptography-layer)
3. [Giao thức Truyền thông & Kiểm soát Tốc độ Mạng (Network Flow & Rate Limiting)](#3-giao-thức-truyền-thông--kiểm-soát-tốc-độ-mạng-network-flow--rate-limiting)
4. [Máy trạng thái Đồng thuận Tendermint BFT & 7 Quy tắc Khóa (Consensus FSM & 7 Rules)](#4-máy-trạng-thái-đồng-thuận-tendermint-bft--7-quy-tắc-khóa-consensus-fsm--7-rules)
5. [Vòng đời Khối Hoàn chỉnh: Hai chặng Header-First & Hai pha Bỏ phiếu (End-to-End Sequence)](#5-vòng-đời-khối-hoàn-chỉnh-hai-chặng-header-first--hai-pha-bỏ-phiếu-end-to-end-sequence)
6. [Tầng Thực thi & Cam kết Trạng thái Merkle (Execution Engine & State Commitment)](#6-tầng-thực-thi--cam-kết-trạng-thái-merkle-execution-engine--state-commitment)
7. [Mô hình Phòng chống Lỗi Byzantine & An toàn Đồng thuận (Fault Model & Byzantine Defense)](#7-mô-hình-phòng-chống-lỗi-byzantine--an-toàn-đồng-thuận-fault-model--byzantine-defense)
8. [Kiến trúc Kiểm thử Tất định T1 – T8 & Tái hiện Trạng thái (Deterministic Test Runner)](#8-kiến-trúc-kiểm-thử-tất-định-t1--t8--tái-hiện-trạng-thái-deterministic-test-runner)

---

## 1. Tổng quan Kiến trúc Nút mạng Toàn diện (Node System Architecture)

Hệ thống mô phỏng một mạng lưới Blockchain Layer 1 gồm $n$ validator cố định ($n \ge 8, n = 3f + 1$, cho phép tối đa $f$ nút Byzantine). Mỗi validator vận hành độc lập, không chia sẻ bộ nhớ, giao tiếp hoàn toàn thông qua mạng mô phỏng phi đồng bộ từng phần (*Partially Synchronous Network*).

```mermaid
graph TB
    subgraph ValidatorNode["Validator Node (Kiến trúc Tổng thể 5 Phân hệ)"]
        direction TB

        %% TV3
        subgraph TV3["1. Tầng Giao tiếp Mạng (TV3 - Network Layer)"]
            direction LR
            P2P["P2P Gossip Engine<br/>(Header-First Diffusion)"]
            RateLimiter["Rate Limiter & Overactive Peer Blocker"]
            NetLogger["Network Event Logger<br/>(send, drop, delay, block, unblock)"]
            InboundQueue["Inbound Priority Queue"]
        end

        %% TV4
        subgraph TV4["2. Tầng Máy Đồng thuận (TV4 - Consensus Engine)"]
            direction LR
            FSM["Consensus FSM<br/>(NEW_ROUND, PROPOSE, PREVOTE, PRECOMMIT, COMMIT)"]
            TimeoutEngine["Deterministic Timer Engine<br/>(tau_propose, tau_prevote, tau_precommit)"]
            ProposerPicker["Proposer Selector:<br/>p = (height + round) mod n"]
            VoteCollector["Vote Registry & Double-Vote Guard<br/>(1 vote / validator / phase)"]
        end

        %% TV5
        subgraph TV5["3. Tầng An toàn & Giám sát (TV5 - Security & Observability)"]
            direction LR
            LockGuard["Tendermint Locking Engine<br/>(PoLC & Rules 1-7 Verification)"]
            ByzantineInjector["Byzantine Adversary Simulator<br/>(Equivocation, Silent, Invalid Block)"]
            ReplayAudit["Deterministic Trace Logger<br/>(Byte-identical rerun verification)"]
        end

        %% TV2
        subgraph TV2["4. Tầng Thực thi & Sổ cái (TV2 - Execution & Ledger)"]
            direction LR
            StateEngine["StateEngine (KV Store)<br/>- Ownership Check ('Sender/')<br/>- Replay Guard (Nonce & TxHash)"]
            MerkleTree["StateCommitment<br/>(Canonical Sorted Merkle Hash)"]
            LedgerView["Ledger View<br/>(Ordered Finalized Blocks & History)"]
            Mempool["Mempool (Pending Valid Txs)"]
        end

        %% TV1
        subgraph TV1["5. Tầng Mật mã & Dữ liệu Nền tảng (TV1 - Crypto & Models)"]
            direction LR
            Ed25519Engine["Ed25519 Engine & Key Registry<br/>(Sign / Verify)"]
            DomainSeparator["Domain Separation Manager<br/>(TX, HEADER, VOTE : chain_id)"]
            CanonicalEncoder["Deterministic Canonical Serializer<br/>(Big-endian, Length-prefix, Sorted map)"]
        end
    end

    %% Tương tác liên tầng
    TV3 <-->|"Gossip Messages (Header, Body, Votes)"| TV4
    TV4 <-->|"Xác thực điều kiện khóa (Lock/Unlock)"| TV5
    TV4 -->|"1. Trích xuất Txs đóng khối"| Mempool
    TV4 -->|"2. Dry-run tính state_hash"| StateEngine
    StateEngine -->|"3. Tính toán gốc Merkle"| MerkleTree
    MerkleTree -->|"4. Trả về state_root"| TV4
    TV4 -->|"5. Commit Block (>= 2f+1 Precommits)"| LedgerView
    LedgerView -->|"6. Cập nhật trạng thái vĩnh viễn"| StateEngine
    StateEngine -->|"7. Xóa Tx đã commit"| Mempool
    
    TV4 -.->|"Ký / Xác thực bản tin đồng thuận"| Ed25519Engine
    TV2 -.->|"Mã hóa tất định dữ liệu băm"| CanonicalEncoder
    TV5 -.->|"Ghi vết sự kiện đồng thuận"| ReplayAudit
```

---

## 2. Cấu trúc Dữ liệu, Mật mã & Domain Separation (Data Models & Cryptography Layer)

Theo **Mục 5 của Lab01.pdf**, mọi thông điệp ảnh hưởng đến trạng thái và đồng thuận đều phải được ký số Ed25519 với tiền tố ngữ cảnh (**Domain Separation**) tường minh để ngăn chặn triệt để tấn công Replay chéo giữa các tầng hoặc giữa các chain.

```mermaid
classDiagram
    direction TB

    class Transaction {
        +str sender
        +str key
        +str value
        +int nonce
        +bytes sig
    }

    class BlockHeader {
        +int height
        +int round
        +bytes parent_hash
        +bytes state_hash
        +bytes tx_root
        +str proposer
        +bytes sig
    }

    class BlockBody {
        +bytes header_hash
        +tuple~Transaction~ transactions
    }

    class Block {
        +BlockHeader header
        +BlockBody body
    }

    class Vote {
        +int height
        +int round
        +str phase
        +bytes block_hash
        +str voter
        +bytes sig
    }

    class DomainContext {
        <<enumeration>>
        DOMAIN_TX
        DOMAIN_HEADER
        DOMAIN_VOTE
    }

    Transaction ..> DomainContext : Domain TX
    BlockHeader ..> DomainContext : Domain HEADER
    Vote ..> DomainContext : Domain VOTE
    Block *-- BlockHeader
    Block *-- BlockBody
```

> **Quy tắc mã hóa nhị phân chuẩn hóa (Deterministic Encoding):**
> - **Số nguyên (`int`):** 8-byte Signed Big-Endian (`int.to_bytes(8, byteorder='big', signed=True)`).
> - **Chuỗi ký tự / Mảng byte (`str`, `bytes`):** Độ dài 4-byte Big-Endian tiền tố + payload bytes UTF-8.
> - **Từ điển (`dict`):** Sắp xếp tăng dần theo thứ tự byte chuẩn hóa của khóa trước khi tuần tự hóa.
> - **Khối dữ liệu / Đối tượng (`dataclass`):** Giữ nguyên thứ tự khai báo thuộc tính.

---

## 3. Giao thức Truyền thông & Kiểm soát Tốc độ Mạng (Network Flow & Rate Limiting)

Theo **Mục 6 của Lab01.pdf**, tầng mạng mô phỏng kênh truyền bất định (*unreliable*) với các ràng buộc khắt khe:
1. **Header broadcast trước Body:** Một Node chỉ gửi `BlockBody` khi nút nhận đã nhận và chấp nhận `BlockHeader` tương ứng.
2. **Kiểm soát tốc độ (Rate Limiting) & Chặn peer quá tải (Peer Blocking):** Mỗi node giới hạn lưu lượng phát ra và tạm thời block các peer vượt ngưỡng gửi bản tin.
3. **Nhật ký mạng đầy đủ:** Mọi sự kiện (`send`, `drop`, `delay`, `block`, `unblock`) đều phải được ghi log kèm timestamp, node ID, type và height.

```mermaid
sequenceDiagram
    autonumber
    participant Prop as Proposer (Node A)
    participant OutRate as Outbound Rate Limiter (A)
    participant Net as Simulated Network Bus
    participant InRate as Inbound Rate Limiter (B)
    participant Val as Validator (Node B)
    participant NetLog as Network Event Logger

    Note over Prop: Chuẩn bị phát tán khối mới ở (Height H, Round R)
    Prop->>OutRate: Yêu cầu phát Header(H, R, state_hash)
    
    alt Tốc độ phát vượt ngưỡng cho phép
        OutRate->>NetLog: Log(type='delay', node='A', msg='RateLimitExceeded')
        Note over OutRate: Tạm hoãn trong hàng đợi ưu tiên (Priority Queue)
    else Tốc độ hợp lệ
        OutRate->>Net: Send Header(H, R, state_hash)
        NetLog->>NetLog: Log(type='send', node='A', target='B', height=H)
    end

    Note over Net: Mô phỏng mạng không tin cậy (Delay/Drop ngẫu nhiên trước GST)
    Net->>InRate: Chuyển giao Header tới Node B
    
    alt Node A gửi spam vượt ngưỡng inbound của B
        InRate->>Val: Kích hoạt chặn tạm thời Node A!
        InRate->>NetLog: Log(type='block', node='B', peer='A', height=H)
    else Thông lượng hợp lệ
        InRate->>Val: Chuyển giao Header
        Note over Val: Thẩm định Header:<br/>1. parent_hash khớp đỉnh chuỗi<br/>2. Chữ ký Proposer hợp lệ<br/>3. Height = tip + 1
        
        alt Header Hợp lệ
            Val->>Net: Gửi yêu cầu GetBlockBody(header_hash)
            NetLog->>NetLog: Log(type='send', node='B', target='A', msg='GetBody')
            Net->>Prop: Chuyển yêu cầu GetBlockBody
            Prop->>Net: Phản hồi BlockBody(Txs)
            Net->>Val: Nhận BlockBody(Txs)
            Note over Val: Ghép Header + Body thành Block hoàn chỉnh để thẩm định State!
        else Header Không hợp lệ
            Val->>NetLog: Log(type='drop', node='B', reason='InvalidHeader')
            Note over Val: Hủy bỏ, không yêu cầu Body!
        end
    end
```

---

## 4. Máy trạng thái Đồng thuận Tendermint BFT & 7 Quy tắc Khóa (Consensus FSM & 7 Rules)

Mục **6.1 của Lab01.pdf** đặt ra 7 quy tắc bắt buộc mà mọi validator trung thực phải tuân thủ để đảm bảo tính an toàn (**Safety**) và tính sống (**Liveness**). Sơ đồ dưới đây ánh xạ trực tiếp 7 quy tắc này vào các trạng thái FSM:

```mermaid
stateDiagram-v2
    direction TB

    [*] --> NEW_ROUND: Khởi động (Height H, Round R)
    
    NEW_ROUND --> PROPOSE: Tính Proposer p = (H + R) mod n
    
    state PROPOSE {
        [*] --> ChoProposal: Bật timer tau_propose
        ChoProposal --> NhanProposal: Nhận Header & Body từ Proposer p
        ChoProposal --> TimeoutPropose: Hết hạn tau_propose [Liveness Guard]
    }

    NhanProposal --> PREVOTE: [Rule 3] Chỉ Prevote khối hợp lệ (Parent, Txs, State Root, Sig)
    TimeoutPropose --> PREVOTE: [Rule 1] Ký tối đa 1 PREVOTE cho (H, R) -> Cast PREVOTE(NIL)

    state PREVOTE {
        [*] --> ThuThapPrevote: Bật timer tau_prevote
        ThuThapPrevote --> PolkaSuccess: [Rule 4] Thấy Quorum >= 2f+1 PREVOTE(B) non-NIL (Polka)
        ThuThapPrevote --> PolkaNil: Thấy Quorum >= 2f+1 PREVOTE(NIL)
        ThuThapPrevote --> TimeoutPrevote: Hết hạn timer tau_prevote
    }

    note right of PolkaSuccess
        [Rule 4]: Validator khóa khối B:
        locked_block = B
        locked_round = R
        [Rule 5]: Chỉ mở khóa hoặc chuyển sang khối khác
        nếu thấy Polka mới hơn ở Round R' > R (PoLC)
    end note

    PolkaSuccess --> PRECOMMIT: [Rule 2] Ký tối đa 1 PRECOMMIT cho (H, R) -> Cast PRECOMMIT(B)
    PolkaNil --> PRECOMMIT: [Rule 2] Cast PRECOMMIT(NIL)
    TimeoutPrevote --> PRECOMMIT: [Rule 2] Cast PRECOMMIT(NIL)

    state PRECOMMIT {
        [*] --> ThuThapPrecommit: Bật timer tau_precommit
        ThuThapPrecommit --> FinalizeQuorum: Thấy Quorum >= 2f+1 PRECOMMIT(B)
        ThuThapPrecommit --> PrecommitFail: Thấy Quorum >= 2f+1 PRECOMMIT(NIL)
        ThuThapPrecommit --> TimeoutPrecommit: Hết hạn timer tau_precommit
    }

    FinalizeQuorum --> COMMIT: [SAFETY FINALITY]: Khối B vĩnh viễn được xác nhận
    
    state COMMIT {
        [*] --> GhiLedger: Ghi Block B vào LedgerView
        GhiLedger --> CapNhatState: StateEngine thực thi vĩnh viễn
        CapNhatState --> ChuyenHeight: Height H = H + 1, Round R = 0
    }
    ChuyenHeight --> NEW_ROUND: Bắt đầu chu kỳ cho Height mới

    PrecommitFail --> TuyenRound: Không đạt thỏa hiệp
    TimeoutPrecommit --> TuyenRound: Thiếu vote do mạng trễ

    state TuyenRound {
        [*] --> TangR: Round R = R + 1
    }
    TangR --> NEW_ROUND: Thử lại tại Round mới (Height H giữ nguyên)
```

> **Ánh xạ 7 Quy tắc Khóa & Bỏ phiếu (Locking & Vote Rules - Mục 6.1):**
> 1. **Rule 1:** Chỉ ký tối đa 1 `PREVOTE` cho mỗi cặp `(height, round)`.
> 2. **Rule 2:** Chỉ ký tối đa 1 `PRECOMMIT` cho mỗi cặp `(height, round)`.
> 3. **Rule 3:** Chỉ chấp nhận và Prevote cho khối có parent hợp lệ, Txs hợp lệ, `state_hash` chính xác, đúng proposer và chữ ký chuẩn.
> 4. **Rule 4:** Khi quan sát thấy Quorum $\ge 2f + 1$ `PREVOTE(B)` non-NIL, validator **phải khóa** khối $B$ và ghi nhận `locked_round = round`.
> 5. **Rule 5 (PoLC):** Đã bị khóa ở khối $B$, chỉ được phép Prevote cho khối $B' \neq B$ nếu quan sát thấy một Quorum $\ge 2f + 1$ `PREVOTE(B')` ở một vòng đấu sau ($R' > \text{locked\_round}$).
> 6. **Rule 6:** Tuyệt đối không ký vote sai chain, sai height, sai round, sai phase hoặc sai domain.
> 7. **Rule 7:** Bỏ qua các vote trùng lặp, chỉ đếm tối đa 1 vote hợp lệ từ mỗi validator trên mỗi tọa độ `(height, round, phase)`.

---

## 5. Vòng đời Khối Hoàn chỉnh: Hai chặng Header-First & Hai pha Bỏ phiếu (End-to-End Sequence)

```mermaid
sequenceDiagram
    autonumber
    actor Client as Người dùng / Client
    participant Mempool as Mempool (Local)
    participant Proposer as Proposer Node (Leader)
    participant Net as Simulated Network Bus
    participant Validator as Validator Node (Follower)
    participant StateEngine as State Engine (TV2)
    participant Ledger as Ledger View (TV2)

    Client->>Mempool: Submit Transaction(sender, target, val, nonce, sig)
    Note over Mempool: Xác thực chữ ký Ed25519 (ngữ cảnh TX:chain_id)

    rect rgb(240, 248, 255)
    Note over Proposer: GIAI ĐOẠN 1: ĐÓNG GÓI & THẨM ĐỊNH NỘI TẠI (PROPOSE)
    Proposer->>Mempool: Lấy danh sách giao dịch hợp lệ
    Mempool-->>Proposer: List[Tx1, Tx2, ...]
    Proposer->>StateEngine: validate_and_compute_hash(Txs) [Dry-run]
    Note over StateEngine: Thực thi tạm trên snapshot bộ nhớ:<br/>1. Kiểm tra prefix 'Sender/'<br/>2. Kiểm tra Nonce tăng đơn điệu<br/>3. Tính Merkle State Root
    StateEngine-->>Proposer: Trả về state_root_hash
    Proposer->>Proposer: Tạo Header(H, R, parent_hash, state_root) & Ký Ed25519
    end

    rect rgb(255, 250, 240)
    Note over Proposer, Validator: GIAI ĐOẠN 2: LAN TRUYỀN HAI CHẶNG (HEADER-FIRST DIFFUSION - MỤC 6)
    Proposer->>Net: Broadcast BlockHeader(H, R, state_root, sig)
    Net->>Validator: Chuyển phát BlockHeader
    Note over Validator: Kiểm tra Header: parent_hash == tip_hash, height == tip + 1, đúng Proposer
    Validator->>Net: Gửi yêu cầu GetBlockBody(header_hash)
    Net->>Proposer: Chuyển yêu cầu GetBlockBody
    Proposer->>Net: Trả về BlockBody(transactions)
    Net->>Validator: Chuyển giao BlockBody(transactions)
    end

    rect rgb(245, 255, 250)
    Note over Validator: GIAI ĐOẠN 3: THẨM ĐỊNH KHỐI ĐỘC LẬP (DRY-RUN EXECUTION)
    Validator->>StateEngine: validate_and_compute_hash(Txs) [Dry-run]
    StateEngine-->>Validator: Trả về local_state_root
    Note over Validator: Đối chiếu local_state_root == header.state_root
    end

    rect rgb(255, 245, 245)
    Note over Validator, Net: GIAI ĐOẠN 4: HAI PHA BỎ PHIẾU (TWO-PHASE VOTING)
    alt Khối hợp lệ & Thỏa mãn Rule 3, Rule 5
        Validator->>Net: Broadcast PREVOTE(H, R, block_hash) [Rule 1]
    else Khối không hợp lệ / Sai State Root
        Validator->>Net: Broadcast PREVOTE(H, R, NIL)
    end

    Note over Net, Validator: Gom đủ >= 2f + 1 PREVOTE(block_hash) [Polka Quorum]
    Validator->>Validator: [Rule 4] Cập nhật locked_block = B, locked_round = R
    Validator->>Net: Broadcast PRECOMMIT(H, R, block_hash) [Rule 2]

    Note over Net, Validator: Gom đủ >= 2f + 1 PRECOMMIT(block_hash) [Finalize Quorum]
    end

    rect rgb(240, 255, 240)
    Note over Validator: GIAI ĐOẠN 5: FINALIZATION & COMMIT CHÍNH THỨC
    Validator->>Ledger: commit_block(Block)
    Ledger->>StateEngine: commit_state(Block.body.transactions)
    StateEngine->>Mempool: Xóa các Txs đã được đưa vào sổ cái
    Ledger-->>Validator: Block finalized vĩnh viễn ở Height H!
    end
```

---

## 6. Tầng Thực thi & Cam kết Trạng thái Merkle (Execution Engine & State Commitment)

Mục **4 và Mục 7 của Lab01.pdf** quy định:
- Trạng thái là bảng bản ghi Khóa - Giá trị (`key-value`).
- Mỗi giao dịch chỉ được phép thay đổi phần dữ liệu thuộc quyền sở hữu của người gửi (`Sender/` prefix).
- Mọi node áp dụng cùng một danh sách giao dịch có thứ tự phải sinh ra kết quả trạng thái hoàn toàn giống nhau.
- Không có phí gas hay thực thi song song; mục tiêu trọng tâm là tính đúng đắn và tính đơn định tuyệt đối.

```mermaid
flowchart TD
    StartTxList(["Nhận danh sách Txs từ Proposed Block"]) --> CloneMem["1. Sao chép trạng thái hiện tại: snapshot = copy(current_state)<br/>(Cơ chế Dry-run Isolation)"]
    
    CloneMem --> LoopTx["2. Duyệt tuần tự từng Transaction theo thứ tự trong Block"]
    
    LoopTx --> VerifySig{"3. Xác thực Chữ ký Ed25519:<br/>Domain == 'TX:chain_id'?"}
    VerifySig -- "Sai chữ ký / Sai domain" --> RejectTx["apply_transaction() -> False (Bỏ qua Tx)"]
    
    VerifySig -- "Hợp lệ" --> CheckOwner{"4. Kiểm tra Quyền sở hữu (Key Ownership):<br/>tx.key bắt đầu bằng 'sender/'?"}
    CheckOwner -- "Không (Xâm phạm dữ liệu người khác)" --> RejectOwner["apply_transaction() -> False (Bỏ qua Tx)"]
    
    CheckOwner -- "Hợp lệ" --> CheckNonce{"5. Kiểm tra Nonce & Replay Guard:<br/>- nonce == account_nonces[sender] + 1?<br/>- tx_hash chưa từng xuất hiện?"}
    CheckNonce -- "Trùng lặp / Nonce nhảy cóc" --> RejectNonce["apply_transaction() -> False (Bỏ qua Tx)"]
    
    CheckNonce -- "Hợp lệ" --> ApplyKV["6. Áp dụng cập nhật:<br/>snapshot[tx.key] = tx.value<br/>account_nonces[sender] = tx.nonce<br/>executed_tx_hashes.add(tx_hash)"]
    
    ApplyKV --> NextTx{"Còn Transaction tiếp theo?"}
    NextTx -- "Còn" --> LoopTx
    NextTx -- "Hết Txs" --> CanonicalSort["7. Sắp xếp Từ điển Trạng thái (Canonical Sorting):<br/>Sort key theo thứ tự byte UTF-8 tăng dần"]
    
    CanonicalSort --> CalcMerkle["8. Tính toán Merkle Tree / Trie Root Hash:<br/>Leaf = SHA-256(canonical_encode(key, value))<br/>Node = SHA-256(Left_Child || Right_Child)"]
    
    CalcMerkle --> MatchRoot{"9. Đối chiếu Root:<br/>calculated_root == header.state_hash?"}
    MatchRoot -- "Không khớp" --> BlockInvalid["Đánh dấu khối HỎNG: InvalidStateRoot<br/>-> Cast PREVOTE(NIL)"]
    MatchRoot -- "Khớp 100%" --> BlockValid["Khối HỢP LỆ: State Root chính xác<br/>-> Cast PREVOTE(block_hash)"]

    BlockValid --> QuorumCheck{"10. Đạt Quorum 2f+1 PRECOMMIT?"}
    QuorumCheck -- "Đạt Finality" --> CommitCanonical["11. Cam kết Vĩnh viễn (Commit):<br/>- Ghi đè snapshot thành current_state<br/>- Cập nhật LedgerView chiều cao H<br/>- Xóa Txs khỏi Mempool"]
    QuorumCheck -- "Thất bại / Chuyển Round" --> Rollback["12. Hủy bỏ snapshot tạm<br/>(State gốc được bảo toàn nguyên vẹn)"]
```

---

## 7. Mô hình Phòng chống Lỗi Byzantine & An toàn Đồng thuận (Fault Model & Byzantine Defense)

Theo **Mục 3 của Lab01.pdf**, hệ thống dung lỗi với tối đa $f$ validator Byzantine trong tổng số $n = 3f + 1$ nút:
- **Kẻ Byzantine có thể:** Im lặng (silent/crash), gửi bản tin trùng lặp (duplicate), gửi phiếu bầu mâu thuẫn (equivocation/double-voting), đề xuất khối sai (invalid block), phát lại bản tin cũ (replay).
- **Kẻ Byzantine không thể:** Giả mạo chữ ký của nút trung thực, tìm va chạm hàm băm SHA-256.

### Sơ đồ Cơ chế Chống Phân nhánh (Equivocation Defense via Tendermint Locking)

```mermaid
sequenceDiagram
    autonumber
    participant Attacker as Byzantine Proposer (Node 0)
    participant GroupA as Honest Validators (Tập A: f+1 nodes)
    participant GroupB as Honest Validators (Tập B: f nodes)
    participant LockGuard as Locking & PoLC Rules (TV5)

    Note over Attacker: Mưu đồ tạo 2 nhánh chuỗi (Fork):<br/>Tạo 2 khối khác nhau B và B' ở cùng (Height H, Round 0)
    Attacker->>GroupA: Gửi Proposal(B)
    Attacker->>GroupB: Gửi Proposal(B')

    Note over GroupA: Thẩm định B hợp lệ -> Gửi PREVOTE(B)
    Note over GroupB: Thẩm định B' hợp lệ -> Gửi PREVOTE(B')

    Note over GroupA, GroupB: Tổng phiếu bầu bị phân mảnh:<br/>B chỉ có tối đa 2f phiếu (không đủ quorum 2f+1)<br/>B' chỉ có tối đa 2f phiếu (không đủ quorum 2f+1)

    alt Kịch bản 1: Không khối nào đạt Quorum (Không có Polka)
        GroupA->>GroupA: Hết hạn tau_prevote -> Cast PRECOMMIT(NIL)
        GroupB->>GroupB: Hết hạn tau_prevote -> Cast PRECOMMIT(NIL)
        Note over GroupA, GroupB: Round 0 kết thúc an toàn không có khối nào commit.<br/>Chuyển sang Round 1 với Proposer mới trung thực!
    else Kịch bản 2: B bất ngờ đạt Polka ở Tập A (>= 2f+1 Prevotes nhờ Byzantine vote lén)
        GroupA->>LockGuard: [Rule 4] KHÓA vào B tại Round 0 (locked_block=B, locked_round=0)
        GroupA->>GroupA: Cast PRECOMMIT(B)
        Note over GroupB: Tập B không đủ Precommit -> Timeout -> Round 1
        Note over LockGuard: [Rule 5 BẢO VỆ SAFETY]:<br/>Ở Round 1, nếu Proposer mới đề xuất B', Tập A BẮT BUỘC TỪ CHỐI Prevote cho B'<br/>trừ khi có bằng chứng Quorum Prevote cho B' ở Round R' > 0.<br/>Vì Tập A (f+1 nodes) đã bị khóa ở B, B' KHÔNG THỂ gom đủ 2f+1 phiếu!<br/>-> TRIỆT TIÊU NGUY CƠ PHÂN NHÁNH (FORK)!
    end
```

---

## 8. Kiến trúc Kiểm thử Tất định T1 – T8 & Tái hiện Trạng thái (Deterministic Test Runner)

Theo **Mục 8 và Mục 9 của Lab01.pdf**, hệ thống bắt buộc phải vượt qua bộ kiểm thử E2E gồm 8 kịch bản (T1 $\rightarrow$ T8) và phải chứng minh tính tất định tuyệt đối qua kịch bản chạy lại hai lần cùng một cấu hình hạt giống (`seed` ngẫu nhiên) sinh ra log mạng và mã băm trạng thái giống nhau từng byte (*Byte-identical output*).

```mermaid
graph TD
    subgraph TestSuite["Bộ Kiểm thử Tự động Chuẩn hóa (Unit & E2E)"]
        direction TB

        subgraph UnitTests["1. Kiểm thử Thành phần (Unit Tests)"]
            U1["test_crypto_encoding.py: Ed25519, Domain separation, Canonical bytes"]
            U2["test_state_ledger.py: Nonce, Ownership 'Sender/', Merkle Root"]
            U3["test_consensus_fsm.py: State transitions, Quorum counting"]
            U4["test_locking_and_votes.py: 7 Locking & Voting Rules"]
            U5["test_network.py: Rate limiting, Peer blocking, Event logging"]
        end

        subgraph E2ETests["2. Kiểm thử Tích hợp Đầu-cuối (E2E Integration Tests)"]
            T1["T1: Normal run (No faults) -> Convergence trên cùng Chain & State Hash"]
            T2["T2: Duplicate/Reordered msgs -> Không double-vote, không conflict finality"]
            T3["T3: Invalid signature / Wrong domain -> Bản tin bị từ chối & logged"]
            T4["T4: Replayed / Duplicate tx -> Giao dịch chỉ áp dụng tối đa 1 lần"]
            T5["T5: Drop/Delay before synchrony (GST) -> Safety bảo toàn tuyệt đối"]
            T6["T6: Proposer silent / crashed -> Timeout kích hoạt, Round tiếp tục"]
            T7["T7: Up to f Byzantine equivocate -> Không phân nhánh chuỗi (No Fork)"]
            T8["T8: Same seed rerun twice -> Byte-identical logs & State Hash"]
        end

        subgraph DeterministicEngine["3. Công cụ Kiểm chứng Tất định (scripts/verify_tv5_determinism.py)"]
            Run1["Lần chạy 1 (Seed S) -> Trace Log 1 + State Hash 1"]
            Run2["Lần chạy 2 (Seed S) -> Trace Log 2 + State Hash 2"]
            DiffEngine["Byte-level Diff Checker"]
        end
    end

    E2ETests --> DeterministicEngine
    Run1 --> DiffEngine
    Run2 --> DiffEngine
    DiffEngine --> AssertEqual{"Khớp 100% từng byte?"}
    AssertEqual -- "PASS" --> Verified["Hệ thống đạt chuẩn Tất định (Deterministic Finality)"]
    AssertEqual -- "FAIL" --> FlagBug["Phát hiện nguồn bất định (Non-deterministic bug)"]
```

---

## 9. Tổng hợp Ánh xạ Phân công Công việc và Báo cáo

| Phân hệ | Thành viên phụ trách | Mã nguồn chính | Đáp ứng yêu cầu Lab01.pdf | Sơ đồ minh họa |
| :--- | :--- | :--- | :--- | :--- |
| **TV1** | Thành viên 1 | `src/crypto/`, `src/common/serializer.py` | Mục 5: Ed25519, Domain separation (`TX`, `HEADER`, `VOTE`), Canonical Encoding | Sơ đồ 1, 2, 5 |
| **TV2** | Thành viên 2 | `src/execution/state.py`, `commitment.py`, `ledger.py` | Mục 4 & 7: State KV store, `Sender/` ownership, Nonce tracking, Merkle Root | Sơ đồ 1, 5, 6 |
| **TV3** | Thành viên 3 | `src/network/` | Mục 6: Header-first diffusion, Rate limiting, Peer blocking, Event logger | Sơ đồ 1, 3, 5 |
| **TV4** | Thành viên 4 | `src/consensus/` | Mục 2 & 6.1: Tendermint FSM, Quorum voting, Timeout engine, Proposer election | Sơ đồ 1, 4, 5 |
| **TV5** | Thành viên 5 | `src/security/`, `src/observability/` | Mục 3, 6.1, 8, 9: 7 Locking rules, Byzantine Equivocation, Replay audit T1-T8 | Sơ đồ 1, 4, 7, 8 |
