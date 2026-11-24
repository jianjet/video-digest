# Contributing

## How a change gets in
1. Start from the latest: `git fetch origin`, then `git checkout -b my-change origin/main`.
2. Make the change on that branch. One idea per branch. **Never push to `main`.**
3. Run both test suites (SETUP.md step 4). They must end with `OK`. A new feature gets a new test.
4. Add one line to `CHANGELOG.md` under **Unreleased**: what changed, in plain words.
5. `git push -u origin my-change`, then open a pull request into `main` on GitHub. Fill in the template.
6. It gets reviewed, tests are rerun, and it's merged or comes back with notes. Only the owner merges into `main`.

## What gets the closest look
The script reads the Firefox login cookie, so these parts get a line-by-line review:
- anything that reads cookies or Firefox files,
- any new website, server or package the code talks to,
- anything that runs another program or installs something.
Say it in the pull request when your change touches one of these. A change that sends the cookie or the output anywhere won't be merged.

## Never commit
- Anything from the output folder (`~/video-digest`): transcripts, cards, `tools.csv`, digests. It's other people's content.
- Your `goals.md`, `creators.txt`, account names, passwords, cookies or `.env` files.

## Never add
- Liking, following, commenting or DMs.
- Scripted login, or a password prompt.
- Parallel Instagram requests, or a faster pace. One request at a time is what keeps the account alive.

## Good first changes
- Mac and Linux support (the Firefox profile path in `firefox_profile()`).
- Better on-screen text for non-English reels.
- Anything that broke for you during setup: fix SETUP.md.
