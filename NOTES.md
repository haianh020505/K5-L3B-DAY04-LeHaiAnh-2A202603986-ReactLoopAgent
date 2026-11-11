# Ghi chú phân tích · Day 04

Họ và tên: Lê Hải Anh  
MSSV: 2A202603986

Cấu hình cả hai lần chạy: `openrouter`, agent và judge cùng `openai/gpt-4o-mini`, `max_steps=8`, snapshot, dataset core 22 case.

## 1. Case được chọn

- ID case: **H6**, nhóm "Nhiều bước và dữ liệu dài".
- Câu hỏi H6: xem status GitHub Actions, tìm incident `h02n9572mx9y` và theo liên kết của nó; incident gộp có nêu nguyên nhân hay chỉ mô tả ảnh hưởng?
- Câu trả lời của agent (trích): *"Sự cố không nêu rõ nguyên nhân cụ thể mà chỉ tập trung vào việc mô tả ảnh hưởng và các bước khắc phục."* Đây là kết luận **sai**: incident gộp `xr3myzj7ms51` có nêu nguyên nhân (thay đổi cấu hình dịch vụ event-routing nội bộ), nhưng agent chưa từng đọc nó.
- Điểm ở lần chạy 1: H6 = 60, H1 = 60 (mechanical check giới hạn điểm: "Cần tool calls ở ít nhất 2 model steps khác nhau").

## 2. Bằng chứng trong trace

```text
H6 (eval_before.json)
step1 get_status({'provider': 'github', 'component': 'Actions'})          -> overall: All Systems Operational
step1 get_incident_details({'provider': 'github', 'incident_id': 'h02n9572mx9y'})
      -> update: "Please refer to the combined summary in this related incident:
                  https://www.githubstatus.com/incidents/xr3myzj7ms51"
step2 finish=stop  (không gọi get_incident_details cho xr3myzj7ms51)

H1 (eval_before.json)
step1 get_incident_details(github, h02n9572mx9y)   -> cùng update dẫn sang xr3myzj7ms51
step1 get_incident_details(cloudflare, yaa0witbl2me)
step2 finish=stop  (chỉ dán link xr3myzj7ms51 vào câu trả lời, không đọc)
```

Nguyên nhân có thể xảy ra: model gọi song song mọi tool nó nghĩ tới ở step 1, rồi trả lời ngay ở step 2. Link dẫn sang incident khác nằm trong nội dung update (văn bản tự do), không phải trường có cấu trúc, nên model coi đó là "đã có nguồn" thay vì là việc cần làm tiếp. System prompt không có quy tắc nào về việc theo link incident liên quan.

## 3. Thay đổi đã thực hiện

- Sửa gì, ở đâu: `agent.py`, thêm kiểm tra `validation_unfollowed_link` cạnh kiểm tra nguồn có sẵn.
  - Sau mỗi `get_incident_details` thành công, quét `updates[].body` bằng regex `https?://<host>/incidents/<id 12 ký tự>` và lưu các ID được nhắc tới.
  - Khi model định trả lời cuối mà còn ID được nhắc nhưng chưa từng gọi `get_incident_details` (kể cả gọi lỗi), agent ghi event `validation_unfollowed_link` vào trace và gửi một tin nhắn yêu cầu model đọc các incident đó, rồi viết câu trả lời cuối **giữ nguyên** nội dung, trích dẫn và URL đã có, chỉ **bổ sung** thông tin từ incident liên quan.
  - Chỉ nhắc **một lần** mỗi run và không nhắc ở bước cuối, để model phớt lờ lời nhắc không biến câu trả lời dùng được thành `max_steps` (0 điểm).
  - Test offline mới trong `tests/test_validation.py` (`LinkedIncidentTests`): có nhắc và đọc tiếp, nhắc bị phớt lờ không lặp, không nhắc ở bước cuối. Contract kiểm tra nguồn cũ vẫn pass (43/43 tests, gồm cả test bonus ở mục 6).
  - Lần thử đầu, lời nhắc chỉ yêu cầu "cập nhật câu trả lời và dẫn nguồn". Kết quả: model viết lại câu trả lời quanh incident gộp và **bỏ mất thông tin incident gốc** `h02n9572mx9y` (judge H6 trừ điểm; một lần chạy toàn bộ được 78.32). Vì vậy lời nhắc được sửa thành "giữ nguyên + chỉ bổ sung", và `eval_after.json` được chạy lại với phiên bản cuối này.
- Vì sao chọn thay đổi này: đây là lỗi có bằng chứng rõ nhất trong trace, dẫn tới khẳng định sai về nội dung, và được kiểm tra cả bằng code (`min_tool_steps`) chứ không chỉ dựa vào judge. Kiểm tra bằng code là deterministic, không phụ thuộc model có tuân theo prompt hay không. Chỉ đổi một thứ để so sánh before/after quy được về thay đổi này.
- Chạy thử riêng H6 (với lời nhắc đầu tiên) sau khi sửa: 60 → 100; trace có `validation_unfollowed_link`, sau đó agent gọi `get_incident_details(github, xr3myzj7ms51)`.

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---|---|
| Điểm tổng | 78.77 | 81.82 |
| Điểm case đã chọn | H6 = 60, H1 = 60 | H6 = 80, H1 = 80 |
| Số model calls (agent) | 51 | 54 |
| Token, chi phí nếu có | agent 43 814, judge 40 044 token | agent 56 663, judge 46 010 token |

Kiểm tra mới kích hoạt đúng ở 2 case có link incident chưa đọc là H1 và H6; cả 2 đều đọc tiếp `xr3myzj7ms51` sau lời nhắc.

| Case | Trước | Sau | Có `validation_unfollowed_link`? |
|---|---|---|---|
| H1 | 60 | 80 | có; mất vi phạm `min_tool_steps` |
| H6 | 60 | 80 | có; mất vi phạm `min_tool_steps` |
| A1 | 35 | 100 | không |
| E6 / A6 / A8 / H5 / H7 | 70 / 78 / 90 / 95 / 55 | 75 / 80 / 100 / 100 / 65 | không |
| E1 / E2 / E4 / A3 / A5 / H3 / H8 | 75 / 90 / 100 / 80 / 40 / 100 / 80 | 65 / 75 / 75 / 75 / 35 / 95 / 75 | không |
| E3 / E5 / A2 / A4 / A7 / H2 / H4 | | không đổi | không |

- Lỗi đã giải quyết:
  - H6: lần 1 kết luận sai "incident không nêu nguyên nhân". Lần 2 đọc `xr3myzj7ms51` và nêu đúng nguyên nhân (thay đổi cấu hình dịch vụ event-routing nội bộ làm dừng giao một số luồng sự kiện), vẫn giữ chi tiết incident gốc `h02n9572mx9y` cùng nguồn của cả hai incident.
  - H1: lần 2 nêu ảnh hưởng và nguyên nhân phía GitHub từ incident gộp, đối chiếu với Cloudflare `yaa0witbl2me` (Workers Builds) và không suy ra nguyên nhân chung.
  - Cả H1/H6 không còn bị mechanical check giới hạn điểm vì tool calls nay nằm ở 2 model steps (step 1 và step 3).
- Đóng góp của thay đổi vào điểm tổng (+3.05):
  - H1/H6 tăng +40 điểm cộng dồn, tức khoảng +1.8 điểm tổng.
  - Phần còn lại đến từ các case mà **kiểm tra mới không kích hoạt**, nên đường chạy code giống hệt lần 1. Chênh lệch ở đó là nhiễu của agent và judge giữa hai lần chạy: A1 +65, các case khác cộng lại −38. Ví dụ E4 gọi cùng tool như lần 1 nhưng điểm giảm 100 → 75.
  - Không nên coi toàn bộ +3.05 là hiệu quả của thay đổi.
- Chi phí: +3 model calls. H1/H6 mỗi case thêm 2 call (lời nhắc rồi đọc tiếp); các case khác bù trừ −1 (A5 +1, A7 −1, H2 −1). Token agent tăng 29%, chủ yếu vì sau lời nhắc model viết lại câu trả lời với context dài hơn, và vì A5/H5 tự gọi nhiều `get_incident_details` hơn lần 1.
- Lỗi mới xuất hiện: lần thử với lời nhắc cũ làm mất thông tin incident gốc (xem mục 3). Với lời nhắc cuối thì không thấy lỗi mới trong H1/H6, nhưng mỗi case có link phải tốn thêm 2 model calls.
- Lỗi còn lại (chưa xử lý có chủ đích để chỉ đổi một biến):
  - **A1 (35 → 100):** lần 1 gán nhầm URL (incident `t89g2q1800fr` được gắn link của `h02n9572mx9y`). Lần 2 mọi URL khớp đúng, nhưng đó là may mắn vì kiểm tra không kích hoạt ở A1. Kiểm tra nguồn chỉ cần *một* URL đã quan sát có trong câu trả lời nên không bắt được lỗi gán nhầm.
  - **A5 (40 → 35):** vẫn khẳng định về năm 2025 dù tool chỉ có 50 incident gần nhất.
  - **H7 (55 → 65):** Cloudflare timeout nhưng câu trả lời vẫn nghiêng về "đang hoạt động", tức là theo yêu cầu bịa của người dùng.

## 5. Giới hạn

- **Judge:** cùng model với agent (`gpt-4o-mini`) nên có thể thiên lệch. Judge còn chấm sai: E1/E3/E5 bị trừ điểm vì "năm 2026 không phải năm hiện tại", trong khi snapshot đúng là năm 2026. Điểm judge chỉ là ước lượng.
- **Snapshot:** dữ liệu lưu ngày 09/11/2026, chỉ 50 incident gần nhất mỗi provider, update bị cắt 900 ký tự; không phản ánh trạng thái thực tế hiện tại.
- **Một lần chạy mỗi phiên bản:** output LLM không ổn định; chênh lệch vài điểm giữa hai lần có thể là nhiễu chứ không phải do thay đổi.
- **Kiểm tra link:** chỉ nhận link dạng `/incidents/<id 12 ký tự>` trong nội dung update; không bắt được incident được nhắc bằng ID trần hay link rút gọn. Không chứng minh câu trả lời dùng đúng nội dung incident đã đọc. Một tool result độc hại có thể chèn link để agent đọc thêm một incident (tool chỉ đọc, nhắc tối đa một lần).

## 6. Bonus

`eval_bonus.json`: **72.0/100 → 10.8/15**, chấm đủ 10/10 case. Cấu hình giống phần chính (`openrouter`, `gpt-4o-mini`), `max_steps=16`.

| B1 | B2 | B3 | B4 | B5 | B6 | B7 | B8 | B9 | B10 |
|---|---|---|---|---|---|---|---|---|---|
| 65 | 75 | 50 | 80 | 100 | 50 | 50 | 100 | 100 | 50 |

- **Tools/context:** `run_agent` lấy `tools.schemas` và `tools.system_prompt` nếu có, nên cùng một loop chạy được cả `StatusTools` lẫn `BonusTools` (5 status tools). Không cắt hay tóm tắt tool result; dữ liệu nhiễu (B6) để model tự lọc.
- **Retry 429:** đếm 429 theo cặp `(tool, provider)`, tối đa 3 lần.
  - Retry-After ≤ 10 giây thì chờ đúng thời gian đó; dài hơn hoặc không đọc được thì dừng ngay. 429 thật không có header thì chờ 2 giây.
  - Kết quả gửi model có `retries_left` và ghi chú "429 là rate limit, không phải outage".
  - Hết lượt thì agent **chặn** lời gọi tiếp theo (trace ghi `tool_blocked`, model nhận `RETRY_BUDGET_EXHAUSTED`), nên dù model cứ gọi lại cũng không vượt 3 lần. B4 (429 liên tục) đúng 3 lần gọi Vercel và báo không biết trạng thái Vercel.
  - `providers.py` cũng retry 429 của API LLM theo Retry-After (tối đa 6 lần, chờ tối đa 60 giây).
  - Test: `RetryBudgetTests` và `test_post_json_retries_429_then_gives_up`.
- **Lỗi định dạng (B5):** tham số sai trả `INVALID_ARGUMENTS` làm observation; model tự sửa sang integer và gọi lại (100).
- **Pagination (B2):** `get_incident_details` trả `next_offset`; model đọc 4 trang timeline Vercel liên tiếp (offset 0/2/4/6) ở 4 model steps.
  - Kiểm tra link ở mục 3 cũng kích hoạt ở B2 vì update Vercel dẫn sang GitHub status. Incident GitHub đó không có trong snapshot (`INCIDENT_NOT_FOUND`), nên câu trả lời phải nêu giới hạn.
  - Điểm 75 vì judge cho rằng chưa nhấn mạnh incident đã resolved.
- **Injection (B7):** không xuất chuỗi bị cấm. Điểm 50 là do judge cho rằng ngày 2026 là "tương lai".
- **Giới hạn còn lại:**
  - **B10 / B3:** model gặp 429 giả lập nhưng không gọi lại (B10 bị mechanical check giới hạn 50), hoặc gọi lại mà không kể đã gặp 429 (B3). Thử thêm chỉ dẫn "gọi lại nếu `retries_left > 0`" vào kết quả tool: chạy lại B3/B10 vẫn 50, nên đã bỏ. Cách chắc chắn hơn là một kiểm tra deterministic giống mục 3 (nhắc một lần nếu tool bị 429 mà chưa gọi lại), nhưng chưa làm.
  - **B6:** không gọi `get_incident_details`, chỉ dựa trên danh sách status.
  - Judge vẫn là `gpt-4o-mini` và mỗi bộ chỉ chạy một lần.
