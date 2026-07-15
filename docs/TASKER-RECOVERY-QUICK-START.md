# Tasker Location Automation — Quick Recovery

**TL;DR: If Tasker lost your location automation, here's how to get back to running in 2 minutes.**

## The One-Minute Fix

1. On your phone, download this file from the repo:
   - `docs/tasker-jarvis-location-15min.xml`

2. Open **Tasker → Preferences → Misc → Import**
   - Select the XML file
   - Done. Profile and task are restored.

3. Toggle the profile **ON** in the Profiles tab

4. Within 15 minutes, you should see a toast notification: "Location pushed: [your coords]"

## If Import Doesn't Work

Read `TASKER-LOCATION-SETUP.md` → Option B (Manual Setup). Takes 5 minutes.

## Why Keep This in Git

The XML file is the **source of truth**. If Tasker's database gets wiped:
- Phone OS update
- App crash
- Accidental data clear
- Uninstall/reinstall

...you can recover in seconds by re-importing the XML, instead of rebuilding from scratch.

**Bonus:** Version history in Git means you can track changes over time if you modify the profile.

---

**Profile Name:** Location Push Every 15m  
**Task Name:** Push Location to JARVIS  
**Interval:** 900 seconds (15 minutes)  
**Endpoint:** `https://jarvis-mdk.fly.dev/api/location`

