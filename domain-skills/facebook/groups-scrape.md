# Facebook Groups: Bulk Post Collection via GraphQL

Source: the user-provided guide and script (`HUONG_DAN_SCRAPE_FB.md`, `scrape_fb_group.py`).
The script is in this directory. It has not been rerun since it was added to this repository.

To make this note available to the agent, copy `domain-skills/facebook/` to
`$BH_AGENT_WORKSPACE/domain-skills/facebook/` (by default,
`~/.config/browser-harness/agent-workspace/domain-skills/facebook/`) and set `BH_DOMAIN_SKILLS=1`.

## When to use

- Collect dozens or hundreds of posts from a Facebook Group and export them to Excel.
- Use Jev to open the group or operate its interface. Use this script to collect posts from GraphQL responses;
  Jev reads page elements and does not parse GraphQL JSON.

## Requirements

- Chrome is running, the user is signed in to Facebook, and the account belongs to the group if it is private.
  If the user is signed out, stop and ask them to sign in. Do not enter their password.
- `openpyxl` is not declared in this repository's `pyproject.toml`. Supply it with `--with openpyxl`.

## Run

```bash
# From the repository root, after uv sync.
uv run --with openpyxl python domain-skills/facebook/scrape_fb_group.py \
  --url "https://www.facebook.com/groups/<slug>/" --limit 50 --output /path/to/fb_posts.xlsx
```

- `--url` is required. `--limit` defaults to 50. `--output` defaults to `fb_posts.xlsx`.
- Excel columns: No., Posted at (UTC+7), Author, Content, Image URLs, Likes, Comments, Post URL.

## How it works

1. Find an open tab for the group URL with `list_tabs` and `switch_tab`, or open one with `new_tab`.
2. Call `cdp("Network.enable")`, then `drain_events()` to discard earlier events.
3. Scroll by 2,200 pixels, wait 1.8 seconds, and collect `Network.responseReceived` events whose URLs contain
   `graphql`. Read each body with `Network.getResponseBody`.
4. Parse each JSON line and recursively find nodes with `__typename == "Story"`, or with both `comet_sections`
   and `post_id`.
5. Extract the fields from each Story:
   - Author: `actors[0].name`.
   - Timestamp: `creation_time`, falling back to `tracking.page_insights.*.post_context.publish_time`.
   - Content: `comet_sections.content.story.comet_sections.message.rich_message[].text`, with other paths
     including `attached_story` for shared posts.
   - Images: `attachments[].styles.attachment.media` and `all_subattachments`, using the `photo_image`, `image`,
     and `large_share_image` keys.
   - Likes and comments: `comet_ufi_summary_and_actions_renderer.feedback.adaptive_ufi_action_renderers`.
6. Deduplicate by `post_id`. Skip posts that have neither content nor images.
7. Stop at `--limit`. After 12 scrolls without a new post, scroll to the bottom and wait 3 seconds. Stop after
   25 scrolls without a new post.

## Notes and risks

- Facebook's GraphQL response structure can change. If the extracted post count is unexpectedly low, inspect
  a Story node and update the extraction paths based on what you observe.
- Private group posts are visible only to a signed-in member. If the script finds no posts, first check whether
  the tab shows a login screen or group landing page.
- Repeated scrolling can trigger Facebook account limits. Set `--limit` to the number of posts needed.
- Posts contain group members' names and content. Use the data only for the user's stated purpose.

## Troubleshooting

| Symptom | Action |
| :--- | :--- |
| Signed out | Ask the user to sign in to Facebook in Chrome and open the group before running the script. |
| Private group | Confirm that the Chrome account is already a member of the group. |
| No more posts load | Check the connection, reload the group page, and rerun the script. |
| `ModuleNotFoundError: openpyxl` | Rerun with `uv run --with openpyxl ...`. |
