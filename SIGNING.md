# Code signing

Release builds of `Interceptium-Setup-<version>.exe` are signed through
[SignPath Foundation](https://signpath.org) once the project is approved. Until then the workflow builds
unsigned files exactly as before; the signing steps switch on by themselves when the settings below exist.

## One-time setup (after SignPath approves the project)

1. In SignPath, open the Interceptium project and set its **artifact configuration** to:

   ```xml
   <?xml version="1.0" encoding="utf-8"?>
   <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
     <zip-file>
       <pe-file-set>
         <include path="Interceptium-Setup-*.exe" />
         <for-each>
           <authenticode-sign />
         </for-each>
       </pe-file-set>
     </zip-file>
   </artifact-configuration>
   ```

2. In SignPath, add GitHub.com as the project's **trusted build system** and create a **CI user**;
   copy its API token.

3. In GitHub: **Settings > Secrets and variables > Actions**
   - **Secrets** tab: `SIGNPATH_API_TOKEN` = the CI user's API token
   - **Variables** tab:
     - `SIGNPATH_ORGANIZATION_ID` = your SignPath organization ID
     - `SIGNPATH_PROJECT_SLUG` = the project's slug (for example `interceptium`)
     - `SIGNPATH_POLICY_SLUG` = the signing policy's slug (for example `release-signing`)

## How a signed release is made

1. Run **Actions > Build and release > Run workflow** (or push).
2. The `windows-setup` job builds the exe and sends it to SignPath.
3. Approve the request in SignPath. The job waits, downloads the signed exe, checks the signature,
   scans it with Microsoft Defender and runs the install, repair and uninstall test on the signed file.
4. The release job publishes the signed exe.
