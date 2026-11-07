# TV Remote user guide

TV Remote lets you control Roku devices from a computer, phone or tablet. Its main feature is
**channel buttons**: one tap opens a streaming app and goes to a channel, so nobody has to learn each
app's menus.

This guide covers everyday use, then setting things up for someone else, such as a relative who finds
the Roku remote hard to use.

- [Getting started](#getting-started)
- [The remote](#the-remote)
- [Channel buttons](#channel-buttons)
- [Adding and editing channels](#adding-and-editing-channels)
- [Choosing which TVs and apps show](#choosing-which-tvs-and-apps-show)
- [Setting it up for someone else](#setting-it-up-for-someone-else)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)

---

## Getting started

### Opening the remote
- **Website:** on the computer that runs it, start the server (see the main [README](../README.md)),
  then open http://localhost:8000. On a phone or tablet on the same Wi-Fi, use the address the server
  prints, such as `http://192.168.1.161:8000`.
- **Windows app:** open **TV Remote.exe**. It opens maximized and works the same way as the website.

The website and the Windows app keep separate channels and settings. Use **Export** and **Import** to
copy channels between them.

### Connecting to a TV
The first time, TV Remote searches your network and lists the Roku devices it finds. Choose one. If
yours isn't listed, make sure it's on, then type its IP address, which is shown on the Roku under
**Settings > Network > About**.

<img src="images/16-connect.png" alt="Choosing a TV" width="320">

> **Before you start:** on each Roku, go to **Settings > System > Advanced system settings >
> Control by mobile apps** and choose **Permissive**. Otherwise, the Roku may refuse some commands or
> its app list.

---

## The remote

The full remote has the usual Roku buttons, plus your TVs, channel buttons, apps, a box for typing
on the TV, and settings.

![The full remote on a computer](images/01-full-remote-desktop.png)

On a phone, the same sections are stacked so you can scroll through them.

<img src="images/02-full-remote-phone.png" alt="The full remote on a phone" width="320">

| Section | What it does |
|---|---|
| **Turn on / Turn off** | Power. These work on Roku TVs. With a Roku stick, they only control the TV if it supports HDMI-CEC. |
| **Back, Home, arrows, OK** | The same as the physical remote |
| **Replay, Options, playback** | Instant replay, the star (Options) button, rewind, play/pause and fast forward |
| **Volume** | Works on Roku TVs. With a stick, it depends on the TV, the same as power. |
| **TVs** | Switch between your Roku devices |
| **Channels** | One-tap buttons that go to a channel. See below. |
| **Apps** | Open any app on the Roku |
| **Type on the TV** | Type into a search box that's open on the TV |

On a computer, you can also use the keyboard: arrows move, **Enter** is OK, **Backspace** is Back,
**H** is Home, **Space** is play/pause, **+** and **-** change the volume, and **M** mutes.

---

## Channel buttons

Tap a channel button, and TV Remote opens the app and presses the buttons to reach that channel.
A banner shows while it works, and **Stop** cancels it.

<img src="images/04-playing-channel.png" alt="A channel button running" width="320">

When a button finishes, TV Remote checks that video started. If nothing is playing, it shows a
message, and it emails your family if alerts are set up.

### Channels-only view
For a simpler screen, choose **Show only channels** next to the Channels heading. This hides the full
remote and leaves the TVs, **Back**, **Home**, **Start over**, shortcuts and channels. The setting stays
on until you choose **Show full remote**.

<img src="images/03-channels-only.png" alt="Channels-only view" width="320">

- **Start over** stops anything that's running and goes back to the Roku home screen. It's the
  button to press when something seems stuck.
- **Shortcuts** are buttons that aren't channels, such as one that opens a guide. They appear above
  the channels.

---

## Adding and editing channels

### Adding a channel by hand
1. Choose **+ Add channel**.
2. Enter a name, and choose the app the channel is in.
3. Tap the buttons under **Button presses** in the order you'd press them on the remote.
   Nothing is sent to the TV while you do this.
4. Choose **Save**, or **Save and try** to run it on the TV straight away.

<img src="images/07-add-channel.png" alt="Adding a channel by hand" width="380">

| Setting | What it means |
|---|---|
| **TVs** | Which TVs get this button. New buttons go on **All TVs**, including TVs added later. |
| **Wait for app to open** | How long to wait for the app before pressing anything. Raise it if the app is slow to load. |
| **Delay between presses** | The wait added before each new press. Each press also has its own wait, which you can change. |
| **This isn't a channel** | Puts the button under Shortcuts, for things like a guide or menu |
| **Check that video starts** | Warns, and sends an email alert, if no video starts. Turn it off for menu buttons. |
| **Start from the app's home screen** | Resets the app before the presses, so they always start from the same place. Works with Frndly TV, so test it with other apps. |

You can also type letters, such as `metv`, to fill in an app's search box, and move or remove any
press with the arrow and **×** buttons.

To put a button on some TVs only, open **TVs**, uncheck **All TVs**, and check the TVs you want.
The TV you're using is always included.

<img src="images/08-choose-tvs.png" alt="Choosing TVs for a channel" width="380">

### Recording a channel instead
If you'd rather use the remote, choose **Record on TV instead**. The TV opens the app, and every button
you press on this page's remote is recorded, along with the pauses between presses. Choose **Save**
in the banner when you reach the channel.

<img src="images/09-recording.png" alt="Recording a channel" width="320">

Wait until the app has fully loaded before pressing anything. Buttons that would interrupt the
recording, like Home and power, are turned off while recording.

### Editing, copying and arranging
Choose **Edit** next to the Channels heading. Each button then shows:

- **Drag to move:** rearrange buttons. With a mouse, you can also drag buttons without choosing
  Edit. Buttons stay within their own group, Shortcuts or Channels.
- **Edit name, TVs & steps:** open the editor
- **Copy:** start a new button from this one's settings
- **Record again:** replace the presses by recording them
- **Delete** or **Delete from this TV:** remove it

<img src="images/10-edit-channels.png" alt="Editing channels" width="380">

Changes to a button shared by several TVs apply on all of them.

### Tips for reliable buttons
- **Test each button from different places:** for example, after watching another channel, from
  the Roku home screen, and after restarting the Roku.
- **Test on each TV:** a button recorded on one TV may land differently on another.
- **Leave enough time:** if a button sometimes lands in the wrong place, raise the waits.
- **Re-check after app updates:** a button that misses after an update usually just needs recording
  again.

---

## Choosing which TVs and apps show

### TVs
TV Remote remembers every Roku it finds. To remove one, choose **Edit** in the TVs section, then
**Remove**, and tap again to confirm. This removes that TV's buttons from TV Remote. Searches won't
add it back, but **+ Add a TV** will.

<img src="images/11-edit-tvs.png" alt="Removing a TV" width="380">

### Apps
Choose **Edit** in the Apps section, and uncheck the apps you don't want to see. This is saved for
each TV. Channel buttons can still use hidden apps.

<img src="images/12-apps.png" alt="The Apps section" width="380"> <img src="images/13-choose-apps.png" alt="Choosing apps to show" width="380">

---

## Setting it up for someone else

If you're setting up TV Remote for someone who finds the Roku remote hard to use:

1. **Add a few channel buttons** for what they watch most, and test each one several times.
2. **Set up email alerts** (see [Settings](#settings)) so you hear about problems.
3. **Turn on the lock** with a PIN. Their screen then shows only big TV and channel buttons, plus
   **Back**, **Home** and **Start over**.

<img src="images/05-locked.png" alt="Locked view" width="320">

To make changes later, tap **Family: unlock** at the bottom and enter the PIN. This unlocks only
the device you're using, so their screen stays locked while you help from your own phone or
computer.

<img src="images/06-unlock.png" alt="Unlocking with the PIN" width="380">

They still turn the TV on with its own remote or button, unless it's a Roku TV, which TV Remote can
turn on itself.

---

## Settings

Settings are at the bottom of the full remote, and closed until you open them.

<img src="images/14-settings-closed.png" alt="Settings, closed" width="380">

<img src="images/15-settings-open.png" alt="Settings, open" width="380">

### Export and import channels
**Export channels** saves this TV's buttons to a file. **Import channels** adds buttons from a file,
updating matching ones and leaving the rest. Use this to back up buttons, or to copy them between
the website and the Windows app.

### Lock view
Choose a 4 to 12 digit PIN and choose **Lock**. To turn it off for everyone, unlock first, then choose
**Turn off lock for everyone**. The lock prevents accidental changes, but it isn't a security feature.

### Email alerts
TV Remote can email one or more people when a channel button fails or no video starts. It sends at
most one email per problem every 30 minutes.

1. Under **Send alerts to**, enter one email address per line.
2. Under **Send from**, choose **Gmail**, or **Other email account** to enter mail server details.
3. **For Gmail:** use a separate Gmail account if you can, turn on 2-Step Verification,
   [create an app password](https://myaccount.google.com/apppasswords), and enter it under
   **App password**. Don't use your normal password.
4. Choose **Send test email**, and check that it arrives, including in spam.

Alerts can't report that TV Remote itself, the computer running it, or the internet is down.

---

## Troubleshooting

| Problem | What to try |
|---|---|
| **No TVs found** | Make sure the Roku is on and on the same Wi-Fi network, not a guest network. Then try typing its IP address. |
| **"The TV refused this command"** | On the Roku, set **Control by mobile apps** to **Permissive**. |
| **Apps list shows an error** | Same as above. |
| **A channel button lands in the wrong place** | Raise **Wait for app to open** and the delays, or record it again. For Frndly TV, turn on **Start from the app's home screen**. |
| **"Nothing seems to be playing"** | Press **Start over** and try again. If it keeps happening, the button needs fixing. For menu buttons, turn off **Check that video starts**. |
| **Turn on, Turn off or volume do nothing** | With a Roku stick, these only work if the TV supports HDMI-CEC. Use the TV's own remote or buttons. |
| **Forgot the lock PIN** | Someone with access to the computer running TV Remote can clear the lock in `settings.json`. |
