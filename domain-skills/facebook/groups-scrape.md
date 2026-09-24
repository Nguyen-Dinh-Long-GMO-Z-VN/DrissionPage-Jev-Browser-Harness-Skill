# Facebook Group: thu thập bài viết hàng loạt qua GraphQL

Nguồn: script và hướng dẫn của người dùng (`HUONG_DAN_SCRAPE_FB.md`, `scrape_fb_group.py`).
Script nằm cùng thư mục: `scrape_fb_group.py`. Chưa được chạy lại sau khi đưa vào repo.

Để agent tự đọc ghi chú này, chép thư mục `domain-skills/facebook/` vào `$BH_AGENT_WORKSPACE/domain-skills/facebook/`
(mặc định `~/.config/browser-harness/agent-workspace/`) rồi đặt `BH_DOMAIN_SKILLS=1`.


## Khi nào dùng

- Cần lấy nhiều bài (hàng chục đến hàng trăm) từ một Group và xuất Excel.
- Không dùng Jev cho việc này: Jev đi từng bước theo bảng phần tử và không đọc được JSON GraphQL.
  Dùng Jev để mở nhóm hay thao tác giao diện, dùng script này để thu dữ liệu.

## Yêu cầu

- Chrome đang chạy, đã đăng nhập Facebook, và tài khoản đã là thành viên của nhóm (nhóm kín).
  Chưa đăng nhập: dừng và hỏi người dùng, không nhập mật khẩu hộ.
- `openpyxl` chưa được khai báo trong `pyproject.toml` của jev-ultrafast. Thêm `--with openpyxl` để chắc chắn.

## Chạy

```bash
# từ thư mục gốc của repo (nơi đã `uv sync`)
uv run --with openpyxl python domain-skills/facebook/scrape_fb_group.py \
  --url "https://www.facebook.com/groups/<slug>/" --limit 50 --output /path/to/fb_posts.xlsx
```

- `--url` bắt buộc. `--limit` mặc định 50. `--output` mặc định `fb_posts.xlsx`.
- Cột Excel: STT, Ngày đăng (giờ Việt Nam), Tác giả, Nội dung, Link ảnh, Lượt Like, Lượt Comments, Link bài viết.

## Cách hoạt động (điểm cần nhớ)

1. Tìm tab đã mở đúng URL nhóm (`list_tabs` + `switch_tab`), nếu không có thì `new_tab`.
2. `cdp("Network.enable")` rồi `drain_events()` để bỏ sự kiện cũ.
3. Vòng lặp: `window.scrollBy(0, 2200)`, chờ 1.8 giây, lấy các `Network.responseReceived` có `graphql` trong URL,
   đọc body bằng `Network.getResponseBody`.
4. Body là nhiều dòng JSON. Duyệt đệ quy để tìm node `__typename == "Story"` (hoặc có `comet_sections` và `post_id`).
5. Trích xuất từ node Story:
   - Tác giả: `actors[0].name`.
   - Thời gian: `creation_time`, dự phòng `tracking.page_insights.*.post_context.publish_time`.
   - Nội dung: `comet_sections.content.story.comet_sections.message.rich_message[].text`, có nhiều đường dự phòng,
     gồm `attached_story` cho bài chia sẻ.
   - Ảnh: `attachments[].styles.attachment.media` và `all_subattachments`, các khoá `photo_image`, `image`, `large_share_image`.
   - Like và comment: `comet_ufi_summary_and_actions_renderer.feedback.adaptive_ufi_action_renderers`.
6. Lọc trùng theo `post_id`. Bỏ bài không có cả nội dung lẫn ảnh.
7. Dừng khi đủ `--limit`, hoặc khi đứng yên: sau 12 lần cuộn không thêm bài thì cuộn xuống cuối trang và chờ 3 giây,
   sau 25 lần thì thoát.

## Lưu ý và rủi ro

- Cấu trúc GraphQL của Facebook đổi định kỳ. Nếu số bài trích được ít bất thường, in thử một node Story và cập nhật
  đường dẫn trích xuất, đừng đoán.
- Bài trong nhóm chỉ hiện khi tài khoản đăng nhập là thành viên. Nếu ra 0 bài, kiểm tra trước xem trang có đang ở
  màn hình đăng nhập hoặc trang giới thiệu nhóm hay không.
- Cuộn liên tục và nhiều lần có thể khiến Facebook giới hạn tài khoản. Dùng `--limit` vừa đủ.
- Dữ liệu chứa tên và nội dung của thành viên nhóm. Chỉ dùng cho mục đích người dùng đã nói rõ.

## Xử lý sự cố

| Triệu chứng | Việc cần làm |
| :--- | :--- |
| Chưa đăng nhập | Mở Chrome, đăng nhập và vào nhóm trước khi chạy |
| Nhóm kín | Tài khoản trong Chrome phải đã tham gia nhóm |
| Không tải thêm bài | Kiểm tra mạng, tải lại trang nhóm rồi chạy lại |
| `ModuleNotFoundError: openpyxl` | Chạy lại với `uv run --with openpyxl ...` |
