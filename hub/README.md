# TV Remote family hub

The family hub lets invited family members use a relative's TV Remote from anywhere: press buttons,
play channels, and change buttons and schedules. It runs in Docker on a NAS.
See [the design](../docs/HOSTED-DESIGN.md) for how it works and why.

```
Family member's browser → https://yourname.ddns.net:8443 → router → NAS: hub container
TV Remote at the relative's home → (asks the hub for work, over HTTPS) → hub container
```

TV Remote at the home only makes outgoing connections, so nothing changes on that home's router.

## Install on the NAS

Everything goes in **one image**: the hub, plus Caddy, which handles HTTPS certificates. No
settings are inside it. You enter them on the hub's **setup page** after the container starts, and
they're saved in the container's `/data` folder.

### What you need
- **A PC with Docker Desktop** installed and running
- **Your No-IP name,** like `yourname.ddns.net`, pointing at the NAS's house
- **Your NAS's processor type.** Most UGREEN NASync models are Intel.

### 1. Build the image file on the PC
In PowerShell, run:
```
powershell -ExecutionPolicy Bypass -File C:\repos\roku-remote\hub\build-image.ps1
```
It asks only for the NAS's processor type, then builds `tvremote-hub.tar` in
`C:\repos\roku-remote`. The first build takes a few minutes.

### 2. Import it on the NAS
1. Copy `tvremote-hub.tar` to the NAS.
2. In UGOS Pro's Docker app, go to **Images** and import the file. The Import under **Containers**
   is something else: it takes a JSON settings file.

### 3. Create the container
In the Docker app, create a container from the **tvremote-hub** image, with:

| Setting | Value |
|---|---|
| **Ports** | `8443` → `8443`, `8088` → `80`, and `8080` → `8080` (NAS port → container port) |
| **Storage** | A NAS folder, like `docker/tvremote-hub` → `/data` (read and write) |
| **Restart** | Always, or Unless stopped |

There's nothing else to set: no environment variables.

**Or, as a Project:** in the Docker app, create a project and paste in
[`docker-compose.nas.yml`](docker-compose.nas.yml). It sets the same ports and folder.

If the NAS already uses one of those ports, pick a different NAS port, like `8088` → `80` or
`8090` → `8080`. Forward the router to the NAS port you picked. If a port is taken, the container
won't start at all, so every port gives "connection refused".

**On a UGREEN NAS, port 80 is taken:** UGOS uses it to redirect to its admin page. Use `8088` → `80`
for the container, and have the router forward external port 80 to the NAS's port 8088.

### 4. Fill in the setup page
On a computer or phone at the NAS's house, open `http://<NAS address>:8080`, using the NAS's
address on your home network, like `http://192.168.1.50:8080`. Enter:
- your No-IP name, and the port (8443)
- **your account:** your name, email and a password. You sign in to the hub with these, from
  anywhere.
- **a setup password,** which protects this page. It's separate from your account password.

Choose **Save and start**. The hub saves the settings in `/data` and starts HTTPS for your No-IP
name.

**If you forget your account password,** open the setup page, enter your setup password, type a new
account password, and save.

**Port 8080 is for your home network only.** Never forward it on the router.

### 5. Forward ports on the router
At the NAS's house:

| External port | To | Purpose |
|---|---|---|
| 8443 (TCP) | The NAS, port 8443 | The family hub |
| 80 (TCP) | The NAS, port 8088 | Certificate checks only |

Turn off UPnP on the router and the NAS. Don't forward 8080.

### 6. Check it
1. **On the setup page,** the status should change to **HTTPS: ready, with a valid certificate**.
   That can take a minute the first time, and needs the port forwards from step 5. If it shows a
   problem instead, check the forwards, and that your No-IP name points at this house.
2. **From your phone with Wi-Fi off,** open `https://yourname.ddns.net:8443`. You should see the
   sign-in page, with a valid certificate.

### Changing settings
Open the setup page again, enter your setup password, change what you need, and choose **Save
changes**. There's no need to rebuild anything.

### Updating the hub
1. Run `build-image.ps1` again, and import the new `tvremote-hub.tar`.
2. Recreate the container from the new image, with the same ports and the same `/data` folder.

Settings, accounts, homes and certificates are all kept in `/data`, so they carry over.

## Link a home and invite family

1. **Sign in** at your hub address with the email and password from the setup page.
2. **At the relative's home,** in TV Remote: open **Settings**, then **Family account**, enter the hub
   address, and choose **Connect**. A 6-digit code appears.
3. **On the hub,** under **Link a home**, enter that code and a name like "Aunt Mary's home". You're
   now its owner, and TV Remote there shows **Connected** within a few seconds.
4. **To invite someone,** choose **Manage** on the home, pick **Helper** or **Owner**, and choose
   **Create invite link**. Send them the link by text or email. It works once, for 7 days.
5. **They open the link,** enter their name and email, and choose a password. After that, they sign
   in with their email and password. A sign-in lasts 90 days on each phone or computer.

**If someone forgets their password,** choose **Manage** on the home, then **Reset password** next to
them, and send them that link. It works once, for 3 days. Accounts made before passwords existed
show "No password yet": send those people a reset link too.

Anyone can change their own password with **Change password** at the top of the hub page.

| Role | Can |
|---|---|
| **Helper** | Use the remote, play channels, and change buttons, schedules and which apps show |
| **Owner** | Everything a helper can, plus invite and remove people, remove TVs, change the lock and email alerts, and remove the home |

Nobody can search the home's network, export or import channel files, or change the Family account
link from the hub. Those only work at the home.

## Good to know

- **TV Remote at the home must be running,** on a computer that doesn't sleep, for family to reach it.
  The hub shows each home as **Connected** or **Not connected**.
- **The relative's screen shows "<name> is helping: ..."** for a few seconds whenever a family member
  does something.
- **Owners see recent activity** under **Manage**, such as "Nephew: Played MeTv on Den".
- **To end remote access:** remove the home on the hub, or choose **Disconnect** in TV Remote's
  Family account settings at the home.
- **Keep it updated.** Rebuilding the image now and then also picks up security fixes in Python and
  Caddy.
- **Inside the container,** the hub and Caddy run as an ordinary user. Only Caddy's ports are
  reachable. The settings file can be read only by the hub.

## What's here

| File | Purpose |
|---|---|
| `hub.py` | The hub. Python's standard library only, with an SQLite database. |
| `portal.html` | Sign-in, homes, invites, people and activity |
| `setup.html` | The setup page, on port 8080, for the home network only |
| `build-image.ps1` | Builds the all-in-one image file for the NAS |
| `Dockerfile`, `start.sh` | The image: Python, the hub and Caddy. The hub starts Caddy once it's set up. |
| `docker-compose.nas.yml` | A project file for that image, with its ports and folder |
| `docker-compose.yml`, `.env.example`, `Caddyfile.example` | An alternative for people comfortable with Compose: settings come from environment variables in `.env`, the hub runs on the official Python image with the code mounted in, and Caddy runs in a second container |

## Testing on one computer

Without Docker, from the `hub` folder:

```
set PUBLIC_URL=http://127.0.0.1:8020
set OWNER_EMAIL=you@example.com
set OWNER_NAME=You
set OWNER_PASSWORD=a-test-password
python hub.py --port 8020 --host 127.0.0.1
```

TV Remote accepts a plain `http://` hub address only for `localhost` or `127.0.0.1`.
