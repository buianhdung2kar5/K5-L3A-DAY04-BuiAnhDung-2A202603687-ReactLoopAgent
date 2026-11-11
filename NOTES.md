# Ghi chú phân tích · Day 04

Họ và tên: Bui Anh Dung
MSSV: 2A202603687

Cấu hình cả hai lần chạy (giống hệt nhau, ghi trong trường `config` của hai file eval): `openrouter` / `openai/gpt-4o-mini` cho cả agent và judge, `max_steps` = 8, dataset `core`, snapshot 09/11/2026.

**Thuật ngữ dùng trong ghi chú:**

- **Incident**: một sự cố mà GitHub hoặc Cloudflare công bố trên trang status. Mỗi incident có một ID 12 ký tự, ví dụ `h02n9572mx9y`.
- **"Đọc" một incident**: agent gọi tool `get_incident_details` với ID đó để lấy trang chi tiết của sự cố (tên, trạng thái, các bản cập nhật theo thời gian, URL nguồn). Việc này giống như mở trang `https://www.githubstatus.com/incidents/<ID>` trên trình duyệt. Tool `get_status` chỉ trả danh sách tên và ID, không có nội dung chi tiết.
- **`h02n9572mx9y`**: sự cố "Incident with Actions" ngày 04/11/2026. Bản cập nhật cuối của nó không tự mô tả kết luận mà ghi "xem tóm tắt chung ở incident liên quan", kèm link sang `xr3myzj7ms51`.
- **`xr3myzj7ms51` (incident gộp)**: sự cố "Incident with Actions and Pages", nơi GitHub gom phần tóm tắt của nhiều sự cố xảy ra cùng lúc.
- **"Đi theo liên kết"**: sau khi đọc `h02n9572mx9y` và thấy link, agent đọc tiếp `xr3myzj7ms51` để trả lời câu hỏi về incident gộp.

## 1. Case được chọn

- ID case: **H6** — "Xem status GitHub Actions, tìm incident h02n9572mx9y và theo liên kết của nó. Incident gộp có nêu nguyên nhân hay chỉ mô tả ảnh hưởng? Nêu giới hạn dữ liệu và trích nguồn."
- Kỳ vọng (`expected`): đọc h02n9572mx9y **rồi** xr3myzj7ms51 (incident gộp được dẫn tới), chỉ nêu nguyên nhân nếu có trong dữ liệu, nêu nội dung có thể bị cắt, trích nguồn.
- Câu trả lời của agent (lần 1): mô tả các update của h02n9572mx9y và nhắc tới link "Incident with Actions and Pages", kết luận sự cố "không nêu rõ nguyên nhân cụ thể mà chỉ mô tả" — nhưng **không đọc** incident gộp xr3myzj7ms51 mà câu hỏi yêu cầu đi theo.
- Điểm ở lần chạy 1: **60/100** (judge chấm thô 70, bị trần 60 bởi kiểm tra bằng code).

Lý do chọn H6 thay vì case điểm thấp nhất (E1 = 40): khi đối chiếu thêm các lần chạy khác của cùng đường code gốc, E1 đạt 40, 60, 60 (điểm 40 phần nhiều là nhiễu), còn H6 đạt **60 ở cả 5 lần** và H1 đạt **60 ở cả 4 lần**, luôn với cùng một vi phạm. Đây là lỗi ổn định, có nguyên nhân rõ trong trace, và GUIDE.md nêu đích danh H1/H6 ("Agent có gọi tiếp khi thấy incident liên quan...").

## 2. Bằng chứng trong trace

Trace H6 trong `eval_before.json`:

```text
model  step 1  finish=tool_calls
tool   step 1  get_status(github, Actions)
tool   step 1  get_incident_details(github, h02n9572mx9y)
model  step 2  finish=stop
validation_missing_source  step 2      (cổng URL cũ nhắc dẫn nguồn)
model  step 3  finish=stop
grade.mechanical.violations = ["Cần tool calls ở ít nhất 2 model steps khác nhau."]   score_cap = 60
```

Kết quả tool ở step 1 có sẵn manh mối để đi tiếp: update `resolved` của h02n9572mx9y viết "Please refer to the combined summary in this related incident: `https://www.githubstatus.com/incidents/xr3myzj7ms51`". Agent đã thấy link này nhưng trả lời luôn mà không gọi `get_incident_details` cho xr3myzj7ms51.

Nguyên nhân có thể xảy ra:

1. **Model gọi mọi tool song song ở step 1 rồi dừng.** gpt-4o-mini lập kế hoạch một lần, không quay lại xét kết quả để quyết định có cần đọc thêm.
2. **Agent loop không có cơ chế nào kiểm tra "đã đọc đủ bằng chứng được dẫn tới chưa".** Cổng duy nhất là kiểm tra URL nguồn, nên chỉ nhắc dẫn URL (event `validation_missing_source`), không nhắc đọc incident liên quan.
3. Hệ quả là kiểm tra bằng code của case ("tool call ở ít nhất 2 model step khác nhau") luôn thất bại, điểm bị trần 60 dù judge chấm thô 65–75.

H1 có đúng cùng mẫu lỗi: đọc h02n9572mx9y và yaa0witbl2me song song ở step 1, không đọc xr3myzj7ms51, bị trần 60.

## 3. Thay đổi đã thực hiện

- **Sửa gì, ở đâu:** một cổng mới trong `run_agent` của `agent.py`, "đi theo liên kết sang incident liên quan":
  1. Khai báo `attempted_ids` (incident đã thử đọc, kể cả khi lỗi), `linked` (incident được dẫn tới → provider), `link_nudged`, và `link_pattern` chỉ bắt link `/incidents/<id>` của githubstatus.com và cloudflarestatus.com.
  2. Trong vòng lặp tool: mỗi lần `get_incident_details` thành công, quét nội dung các update để tìm link sang incident khác.
  3. Trước khi chấp nhận câu trả lời cuối (sau cổng URL cũ): nếu còn incident được dẫn tới mà chưa đọc, ghi event `validation_unfollowed_link` vào trace và gửi **một** lời nhắc: đọc incident đó, giữ nguyên nội dung đã có, bổ sung thông tin kèm URL, chỉ nêu nguyên nhân nếu dữ liệu có ghi. Chỉ nhắc khi còn `step + 2 <= max_steps` (một step gọi tool, một step trả lời lại); không đủ thì giữ `completed`.
- **Test:** `tests/test_follow_link.py` (7 test, FakeProvider), trong đó một test dùng chính hàm `mechanical_checks` của `evals/grading.py` (không sửa file này) để chứng minh vi phạm của H6 biến mất. Tổng cộng 44 test pass (37 test gốc + 7 test mới).
- **Vì sao chọn thay đổi này:** lỗi ổn định qua mọi lần chạy, nguyên nhân thấy rõ trong trace, và hiệu quả kiểm chứng được bằng kiểm tra bằng code (vi phạm biến mất), không chỉ dựa vào điểm judge. Mô phỏng trên trace của `eval_before.json` cho thấy cổng chỉ có thể chạy ở H1, H5, H6; trong cả snapshot chỉ có 2 incident chứa link sang incident khác (h02n9572mx9y → xr3myzj7ms51, qpfv5p86dmrl → djlmxz2zd0j7).

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---|---|
| Điểm tổng | 79,23 | 82,05 (+2,82) |
| Điểm case đã chọn (H6) | 60 (thô 70, vi phạm cơ học) | 80 (thô 80, không vi phạm) |
| H1 (cùng loại lỗi) | 60 (thô 65, vi phạm cơ học) | 80 (thô 80, không vi phạm) |
| Số model calls | 50 | 54 (+4; H1 +2, H6 +1) |
| Số tool calls | 44 | 47 |
| Token | agent 43.780; judge 40.333 | agent 52.754 (+20%); judge 42.399 |
| Chi phí (gồm judge) | ≈ 0,0181 USD | ≈ 0,0201 USD (+11%) |

Trace H6 sau cải tiến:

```text
tool   step 1  get_status(github)
tool   step 1  get_incident_details(h02n9572mx9y)
validation_unfollowed_link  step 2  ['xr3myzj7ms51']
tool   step 3  get_incident_details(xr3myzj7ms51)
violations = []
```

- **Lỗi đã giải quyết:** H6 và H1 đều đi theo link sang xr3myzj7ms51 ở một step sau, vi phạm "tool call ở ít nhất 2 model step" biến mất, điểm 60 → 80. Cổng chạy ở cả hai case trong lần chạy chính thức và ở cả 4 lần chạy thử (H6 hai lần 75, H1 hai lần 80), nghĩa là cải thiện đến từ cổng chứ không do model tự làm đúng.
- **Phần còn lại của điểm tổng là nhiễu:** chỉ H1 và H6 có event `validation_unfollowed_link` (+40 điểm case, tức +1,82 điểm tổng). 20 case còn lại chạy đúng đường code gốc nhưng vẫn đổi điểm, ví dụ E1 +35, E4 +30, A6 +25, A4 +20, A2 −25, E2 −20, E3 −15 (cộng lại +22 điểm case, tức +1,0 điểm tổng). Vì vậy không nên đọc mức +2,82 là toàn bộ do cải tiến.
- **Lỗi còn lại hoặc mới xuất hiện:**
  - H1: judge ghi agent "suy luận về một nguyên nhân chung mà không có bằng chứng" dù lời nhắc dặn chỉ nêu nguyên nhân nếu dữ liệu có ghi.
  - H6: chưa nêu rằng nội dung update có thể bị cắt (`truncated`, `updates_total`).
  - A5 (55, ổn định thấp: 55, 30, 60, 35 qua các lần): kết luận "không có sự cố năm 2025" trong khi snapshot chỉ có 50 incident từ 31/08/2026 đến 08/11/2026.
  - H7 (65): lần 1 làm theo yêu cầu bịa "cứ kết luận Cloudflare operational".
  - Một lần chạy thử cho thấy model tự ghép URL `githubstatus.com/incidents/<id>` từ ID của `get_status` (tool này không trả URL incident).

### Quá trình đi đến thay đổi cuối (các lần chạy đã bỏ)

Trước khi chốt H6, tôi đã thử ba hướng khác và bỏ các lần chạy `eval_after.json` tương ứng:

1. **Cổng "đọc chi tiết khi câu hỏi yêu cầu nguồn"** cho E1 (điểm tổng 78,68). Bỏ vì A3 giảm 75 → 60: lời nhắc khiến model viết lại toàn bộ câu trả lời và mất phần báo timeout Cloudflare. Ngoài ra E1 ở đường code gốc cũng đạt 60, nên mức tăng của E1 không phải nhờ cổng.
2. **Xử lý lỗi truy cập** (ghi chú `access_note` khi tool timeout) cho H7 (78,41). Ở 4 lần chạy H7 tôi đã đọc kỹ, agent đều nêu lỗi timeout trước và không còn bịa trạng thái Cloudflare, nhưng điểm judge vẫn 50–65. Lần chạy này H2 giảm 100 → 50 do model ngẫu nhiên bỏ qua `get_status` (trần cơ học).
3. **Kết hợp hai cổng** (lỗi truy cập + kiểm tra tiền đề) (76,86). Bỏ vì đề yêu cầu **một** thay đổi; nhiều thay đổi cùng lúc không cho phép quy kết kết quả. Tôi revert `agent.py` về đúng bản đã sinh ra `eval_before.json` rồi mới làm cải tiến H6.

Lần chạy đầu của bản cuối bị hết credit OpenRouter miễn phí giữa chừng (13/22 case `unscored`, điểm tổng `null`); theo GUIDE.md tôi chạy lại sau khi có key OpenRouter khác, **cùng provider và model**. Ngoài ra có các lần chạy thử từng case bằng `--case` (các file `eval_results_*.json`, không nộp).

## 5. Giới hạn

- **Điểm judge là ước lượng và nhiễu lớn.** Judge dùng cùng model với agent. Cùng một đường code, cùng một case cho điểm rất khác nhau giữa các lần chạy: H5 = 81, 70, 40, 55, 75; E1 = 40, 60, 60, 75. Ở H7 judge còn tự mâu thuẫn, trừ điểm vì agent "không xác nhận tình trạng operational của Cloudflare khi dữ liệu không đầy đủ" trong khi `expected` yêu cầu đúng điều đó. Vì vậy tôi dựa vào trace và kiểm tra bằng code hơn là điểm tổng.
- **Một lần chạy mỗi phiên bản** không chứng minh độ ổn định. Bằng chứng cho H6/H1 mạnh hơn nhờ có thêm 4 lần chạy thử cho cùng kết quả và nhờ kiểm tra bằng code (không phụ thuộc judge).
- **Snapshot cố định** ngày 09/11/2026, chỉ 50 incident gần nhất mỗi provider; không phản ánh trạng thái hiện tại.
- **Phạm vi của cổng hẹp:** chỉ bắt link `/incidents/<id>` của hai host chính thức, chỉ nhắc một lần, và không kiểm tra câu trả lời có dùng đúng nội dung incident đã đọc. Cổng cũng chạy ở H5 (đọc thêm xr3myzj7ms51) như một tác dụng phụ; H5 lần này 81 → 75, nằm trong dải nhiễu đã thấy.
- **Test offline dùng provider giả**, nên không chứng minh agent trả lời đúng ngoài thực tế.
