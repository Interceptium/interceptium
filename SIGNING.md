# Code signing

## Your own (self-signed) certificate

The release workflow can sign `Interceptium-Setup-<version>.exe` with a certificate you made yourself.
Windows only trusts a self-signed certificate on PCs where it is installed, so on other people's PCs
SmartScreen still shows "Unknown publisher". The signature still proves the file wasn't changed after the build.

1. Make a certificate (PowerShell, on your PC):

   ```powershell
   $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=Canopy Interactive" `
     -KeyAlgorithm RSA -KeyLength 3072 -NotAfter (Get-Date).AddYears(5) `
     -CertStoreLocation Cert:\CurrentUser\My
   Export-PfxCertificate -Cert $cert -FilePath "$HOME\Desktop\interceptium-signing.pfx" `
     -Password (Read-Host -AsSecureString "Password for the .pfx")
   ```

2. Copy it as text:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\Desktop\interceptium-signing.pfx")) | Set-Clipboard
   ```

3. In GitHub: **Settings > Secrets and variables > Actions > Secrets** tab, add
   - `SIGNING_PFX_BASE64` = the text from step 2
   - `SIGNING_PFX_PASSWORD` = the password from step 1

4. Run **Actions > Build and release**. The exe is signed, scanned with Microsoft Defender and install-tested.

Keep the .pfx and its password private: never commit them or paste them anywhere. To trust the certificate on
your own PC, double-click the .pfx (or import its public part into *Trusted Publishers* and
*Trusted Root Certification Authorities* for your user).

## SignPath

Instead of your own certificate, the workflow can sign `Interceptium-Setup-<version>.exe` through
[SignPath](https://signpath.io) (the free SignPath Foundation program, which asks for a project with some public
following, or a paid SignPath plan). Until the settings below exist, the signing steps are skipped and the
workflow uses your own certificate if set, or builds unsigned files.

## One-time setup (once a SignPath project exists)

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
