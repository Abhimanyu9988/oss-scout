# A contributor's playbook

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

Lessons from a first month of contributing to OpenTelemetry. They apply to most large open-source projects.

## Before you write any code

1. **Sort out accounts.** Use a personal GitHub account, and sign commits with the email you used for the project's
   Contributor License Agreement. A mismatch fails the CLA check.
2. **Read the contributing guide and any AI policy** (OpenTelemetry's is `AGENTS.md`). The PR template may ask you to
   confirm a human wrote the description.
3. **Check for competing work.** GitHub's "linked PR" filter misses PRs that only mention the issue in their
   description. Search open PRs by issue number, and read the thread for comments like "I'd like to work on this".
4. **Expect dead ends.** On a busy repository, about three candidate issues fall through for every one that's
   really free. oss-scout's board does this filtering for you.
5. **Ask first.** Comment with what you plan to change, and wait for a maintainer before building.

## While you work

6. **Check intent before calling something a bug.** Read what the library or specification says it should do. A
   change a maintainer has to walk back costs more than the check.
7. **Fix the specification first when there is one.** In OpenTelemetry, metric names and meanings live in the
   semantic conventions. Code changes follow.
8. **Keep the change small.** Run the project's generators and linters only on what you touched, and revert
   anything unrelated before you push.
9. **Show your evidence.** Say in the PR and in comments what you ran and what it showed. For a flaky test, run it
   many times with the race detector and say how many.

## Working with others

10. **Review other people's PRs.** Check them out, run their tests and say what you checked. Maintainers are short
    of review time, not of PRs.
11. **When you're second, help the first.** Close your duplicate gracefully, and leave a useful review on the
    original.
12. **Follow through.** If you said you'd review something, do it. oss-scout's board keeps those PRs in front of you
    until you have.

## Good first contributions

- **Documentation** for a problem users keep hitting: a known issue, a workaround, a troubleshooting entry.
- **Flaky tests,** often auto-filed by the project's CI. They're unglamorous, so they're usually free, and they
  teach you the code.
- **Per-component tasks** from a tracking issue, where the work is split so one part is always left.

---

[Back to the README](../README.md)
