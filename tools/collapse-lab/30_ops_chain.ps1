# CLIENT-side ops chain: 11 realistic admin steps over SSH, each a fresh
# session, individually timed. Run against the TARGET during a collapse window.
# Usage:  powershell -File 30_ops_chain.ps1 -Label ON_run1 -StopTarget guardian-oom-swarm-3
# Set $env:LAB_TARGET and $env:LAB_KEY to reuse without editing.
param(
  [string]$Label = "run",
  [string]$StopTarget = "guardian-oom-swarm-3",
  [string]$TargetUser = $(if ($env:LAB_TARGET) { $env:LAB_TARGET } else { "csfang@192.168.225.53" }),
  [string]$KeyPath = $(if ($env:LAB_KEY) { $env:LAB_KEY } else { "$env:USERPROFILE\.ssh\id_ed25519" })
)
$results = @()
function SshStep($label, $cmd) {
    $script:out = $null
    $t = Measure-Command { $script:out = ssh -i $KeyPath -o ConnectTimeout=10 $TargetUser $cmd 2>$null }
    $secs = [math]::Round($t.TotalSeconds, 2)
    $script:results += [pscustomobject]@{ step = $label; seconds = $secs }
    Write-Output ("  {0,8}s  {1}" -f $secs, $label)
}
Write-Output "=== ops chain: $Label @ $(Get-Date -Format 'HH:mm:ss') ==="
SshStep "login+uptime"            "uptime"
SshStep "free"                    "free -m | sed -n 2,3p"
SshStep "df"                      "df -h / | tail -1"
SshStep "dmesg tail"              "dmesg --ctime | tail -3"
SshStep "journalctl"              "journalctl -n 3 --no-pager | tail -3"
SshStep "top-mem proc"            "ps aux --sort=-rss | head -4 | tail -3"
SshStep "docker ps"               "docker ps --format {{.Names}} | wc -l"
SshStep "docker stats (heavy)"    "docker stats --no-stream --format {{.Name}}:{{.MemUsage}} | head -6"
SshStep "STOP one stressor (t=5)" "docker stop --time 5 $StopTarget"
SshStep "verify: free"            "free -m | sed -n 2p"
SshStep "verify: docker ps"       "docker ps --format {{.Names}} | wc -l"
$total = ($results | Measure-Object seconds -Sum).Sum
$avg   = ($results | Where-Object { $_.step -notmatch "STOP" } | Measure-Object seconds -Average).Average
Write-Output ("TOTAL: {0}s   (non-STOP avg {1}s)" -f ([math]::Round($total,1)), ([math]::Round($avg,2)))
$results | ForEach-Object { "{0},{1}" -f $_.step, $_.seconds } |
  Out-File -Append -Encoding utf8 ("$env:TEMP\collapse_ops_{0}.csv" -f $Label)
