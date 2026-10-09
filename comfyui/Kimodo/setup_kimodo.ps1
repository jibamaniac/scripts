# Writes kimodo_config.json for the ComfyUI-Kimodo nodes.
# Run it through setup_kimodo.bat (double-click), or:
#   powershell -ExecutionPolicy Bypass -File setup_kimodo.ps1 [path\to\kimodo]
# It finds the Kimodo checkout and the venv Python that has Kimodo installed,
# checks them, and saves both next to the nodes. Nothing else is changed.
param([string]$KimodoPath = "")

$ErrorActionPreference = "Stop"
$NodeDir = $PSScriptRoot
$ConfigPath = Join-Path $NodeDir "kimodo_config.json"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

function Clean-Path($p) {
    if (-not $p) { return "" }
    return $p.Trim().Trim('"').Trim()
}

# The checkout is the folder that holds the kimodo package (kimodo\__init__.py).
# Accept that folder, its parent (e.g. E:\projects\kimodo with the checkout in
# E:\projects\kimodo\kimodo), or anything one level above that.
function Find-Repo($folder) {
    $folder = Clean-Path $folder
    if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { return $null }
    $folder = (Resolve-Path -LiteralPath $folder).Path
    if (Test-Path -LiteralPath (Join-Path (Join-Path $folder "kimodo") "__init__.py")) { return $folder }
    foreach ($child in Get-ChildItem -LiteralPath $folder -Directory -ErrorAction SilentlyContinue) {
        if (Test-Path -LiteralPath (Join-Path (Join-Path $child.FullName "kimodo") "__init__.py")) { return $child.FullName }
    }
    return $null
}

# Same places the nodes look: venv\ or .venv\ in the checkout or its parent folder.
function Find-Python($repo) {
    $parent = Split-Path -Parent $repo
    foreach ($base in @($repo, $parent)) {
        if (-not $base) { continue }
        foreach ($venv in @("venv", ".venv")) {
            foreach ($rel in @("Scripts\python.exe", "bin/python")) {
                $p = Join-Path (Join-Path $base $venv) $rel
                if (Test-Path -LiteralPath $p -PathType Leaf) { return (Resolve-Path -LiteralPath $p).Path }
            }
        }
    }
    return $null
}

function Pick-Folder($title) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = $title
        $dialog.ShowNewFolderButton = $false
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.SelectedPath }
        return ""
    } catch {
        return Clean-Path (Read-Host "$title (paste the full path)")
    }
}

function Pick-File($title) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = $title
        $dialog.Filter = "python.exe|python.exe|All files|*.*"
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.FileName }
        return ""
    } catch {
        return Clean-Path (Read-Host "$title (paste the full path)")
    }
}

function Ask-YesNo($question) {
    $answer = Read-Host "$question [Y/n]"
    return (-not $answer) -or ($answer.Trim().ToLower().StartsWith("y"))
}

# Places Kimodo is often installed, checked before asking.
function Candidate-Folders {
    $list = New-Object System.Collections.Generic.List[string]
    if ($KimodoPath) { $list.Add($KimodoPath) }
    if ($env:KIMODO_REPO) { $list.Add($env:KIMODO_REPO) }
    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $old = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
            if ($old.kimodo_repo) { $list.Add([string]$old.kimodo_repo) }
        } catch { }
    }
    # Next to ComfyUI: walk up from custom_nodes\ComfyUI-Kimodo and look for a kimodo sibling.
    $dir = $NodeDir
    for ($i = 0; $i -lt 5 -and $dir; $i++) {
        $dir = Split-Path -Parent $dir
        if ($dir) { $list.Add((Join-Path $dir "kimodo")) }
    }
    if ($env:USERPROFILE) {
        foreach ($rel in @("kimodo", "Documents\kimodo", "Desktop\kimodo", "projects\kimodo")) {
            $list.Add((Join-Path $env:USERPROFILE $rel))
        }
    }
    foreach ($drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        foreach ($rel in @("kimodo", "projects\kimodo", "AI\kimodo", "ai\kimodo")) {
            $list.Add((Join-Path $drive.Root $rel))
        }
    }
    return $list
}

Say ""
Say "ComfyUI-Kimodo setup" "Cyan"
Say "Finds your Kimodo install and writes $ConfigPath"
Say ""

# 1. Kimodo checkout
$repo = $null
foreach ($candidate in Candidate-Folders) {
    $found = Find-Repo $candidate
    if ($found) {
        Say "Found Kimodo at $found" "Green"
        if (Ask-YesNo "Use this one?") { $repo = $found }
        break
    }
}
while (-not $repo) {
    Say "Select the Kimodo folder (the one you cloned or installed Kimodo into)." "Yellow"
    $picked = Pick-Folder "Select the Kimodo folder"
    if (-not $picked) {
        Say "No folder chosen. Nothing was changed." "Red"
        exit 1
    }
    $repo = Find-Repo $picked
    if (-not $repo) {
        Say "No Kimodo code found in $picked." "Red"
        Say "Pick the folder that contains the 'kimodo' package folder (it has kimodo\__init__.py), or the folder above it." "Red"
    }
}

# 2. The Python that has Kimodo installed
$python = Find-Python $repo
if ($python) {
    Say "Found Kimodo's Python at $python" "Green"
} else {
    Say "No venv\ or .venv\ found in $repo or the folder above it." "Yellow"
}
while (-not $python) {
    Say "Select the python.exe of the environment you installed Kimodo into (usually ...\venv\Scripts\python.exe)." "Yellow"
    $picked = Clean-Path (Pick-File "Select Kimodo's python.exe")
    if (-not $picked) {
        Say "No python.exe chosen. Nothing was changed." "Red"
        exit 1
    }
    if (Test-Path -LiteralPath $picked -PathType Leaf) {
        $python = (Resolve-Path -LiteralPath $picked).Path
    } else {
        Say "$picked doesn't exist." "Red"
    }
}

# 3. Check that Python runs and has Kimodo's main dependency (torch).
Say "Checking Kimodo's Python..."
$check = "import importlib.util, sys; sys.path.insert(0, sys.argv[1]); " +
         "missing = [m for m in ('torch', 'kimodo') if importlib.util.find_spec(m) is None]; " +
         "print('missing: ' + ', '.join(missing) if missing else 'ok')"
try {
    $result = & $python -c $check $repo 2>&1 | Out-String
} catch {
    $result = "could not run: $_"
}
$result = $result.Trim()
if ($result -eq "ok") {
    Say "Python works and can see Kimodo and torch." "Green"
} else {
    Say "Warning: $python -> $result" "Yellow"
    Say "Kimodo may not be installed in this environment (see Kimodo's install guide)." "Yellow"
    if (-not (Ask-YesNo "Save these paths anyway?")) {
        Say "Nothing was changed." "Red"
        exit 1
    }
}

# 4. Write kimodo_config.json, keeping any other settings already in it.
$config = [ordered]@{}
if (Test-Path -LiteralPath $ConfigPath) {
    try {
        $old = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        foreach ($prop in $old.PSObject.Properties) { $config[$prop.Name] = $prop.Value }
    } catch {
        Copy-Item -LiteralPath $ConfigPath -Destination "$ConfigPath.bak" -Force
        Say "The old kimodo_config.json couldn't be read; saved a copy as kimodo_config.json.bak." "Yellow"
    }
}
$config["kimodo_repo"] = $repo.Replace("\", "/")
$config["python"] = $python.Replace("\", "/")
$json = ($config | ConvertTo-Json) + "`r`n"
# UTF-8 without a BOM so Python's json module reads it.
[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))

Say ""
Say "Saved $ConfigPath" "Green"
Say $json
Say "Restart ComfyUI to use the Kimodo nodes." "Cyan"
exit 0
