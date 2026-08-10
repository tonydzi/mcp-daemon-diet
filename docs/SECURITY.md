# SECURITY - what changes when a per-session child becomes a shared daemon

The diet does not add capability. It changes **who can reach it, for how long, and with
whose credentials** - read this before you convert a server that holds an account.

## The shape of the change

A stdio server is a child process of one client: it lives as long as that session, and
nothing else on the machine can talk to it. A daemon is a long-lived local network service
holding the same credentials, reachable by **any process running on the machine** - every
app, every script, every dependency of every project you open.

That is the trade. For a server that reads public docs it is nothing. For one holding a
logged-in messenger account it is the whole security story.

## Non-negotiables

1. **Bind `127.0.0.1`, never `0.0.0.0`.** A bind to all interfaces publishes your account
   to the local network - the coffee-shop Wi-Fi included. Check with `netstat -an`, don't
   assume: some servers default to all interfaces and only accept a host flag if you pass
   it explicitly.
2. **Localhost is not an authorization boundary.** Any local process can connect. If the
   server supports a bearer token, set one, even locally. Two of the four we run do.
3. **No secrets in the launcher if the launcher is committed or synced.** Read them from a
   file outside the tree (the Windows template shows the pattern) or from the user
   environment. A launcher is a script; scripts end up in git.
4. **Environment variables are not private.** Anything that can list your processes can
   read them - `Get-CimInstance Win32_Process` on Windows, `/proc/<pid>/environ` on Linux.
   A shared machine is a shared credential.
5. **Do not run the daemon as a service account or as root** just to make autostart
   simpler. User-level autostart exists everywhere (Run key, launchd agent,
   `systemd --user`) and keeps the blast radius at your own account.

## Consequences most people meet later

- **The daemon outlives the work.** A stdio child dies with the session; a daemon keeps
  your account logged in and reachable 24/7, including while you are away from the
  machine. If that is not what you want, start it when you work and stop it after.
- **One daemon, one audit trail.** Every session's actions now come from one process, so
  upstream logs (message senders, DB clients) can no longer tell your sessions apart.
- **A crash is now everyone's crash.** Every session on the machine loses the connector at
  once. Weigh that against per-session copies for anything you actually depend on.
- **Firewall prompts.** The first bind may raise an OS firewall dialog. Allow **private**
  only, or better, deny it - a `127.0.0.1` listener needs no firewall exception at all.

## Prompt injection is unchanged and still yours to handle

Content a server returns - messages, issues, documents, web pages - is untrusted input,
not instructions. That is true for stdio and daemon alike; the daemon only widens who can
ask. Keep write-capable tools behind explicit confirmation.

## Before you convert a server, ask

Does it hold credentials that are hard to revoke? Can any local process do harm with it in
one call (send as me, delete, spend)? If both are yes, either keep it per-session, or put
a token on it and stop it when you are not working.
