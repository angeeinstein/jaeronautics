#Requires -Version 7.0
<#
.SYNOPSIS
    Checks the portal and the forum from outside, through Cloudflare, the way
    an intake evening will reach them: many readers at once, and the rate
    limits behaving as designed.

.DESCRIPTION
    Run from a PC, not from the servers: the point is the whole path -- DNS,
    Cloudflare, the tunnel, nginx, the application. Nothing is created. The
    login attempts use addresses at example.com that no account has, and the
    signup attempts send only an email address, which the form refuses before
    anything is stored or sent.

    Four checks, each with what it should show:

      1. Portal, reading   -- ReadClients browsers at once for ReadSeconds.
                              Expect only 200s and a p95 latency in the
                              low hundreds of milliseconds.
      2. Portal, login     -- ten wrong guesses at one address are answered,
                              the eleventh is refused (429); forty different
                              addresses from this one network are all answered.
      3. Portal, signup    -- the same for the signup form: the eleventh try
                              with one address is refused, thirty different
                              addresses are not.
      4. Forum, anonymous  -- a burst of anonymous page loads from one address
                              is cut off (429) after roughly fifty in ten
                              seconds, by Discourse or by its nginx.

    Checks 2 to 4 spend this network's allowance for a quarter of an hour:
    the test addresses stay locked, and the forum may answer this address
    with 429 for a minute. Signed-in browser sessions are counted per person
    and are not affected by the forum's limit.

.EXAMPLE
    pwsh ./edge-check.ps1

.EXAMPLE
    pwsh ./edge-check.ps1 -ReadClients 50 -ReadSeconds 120 -SkipForum
#>
param(
    [string]$Portal = "https://members.joanneum-aeronautics.at",
    [string]$Forum = "https://lavboard.joanneum-aeronautics.at",
    [int]$ReadClients = 30,
    [int]$ReadSeconds = 60,
    [int]$ForumBurst = 120,
    [switch]$SkipRead,
    [switch]$SkipLogin,
    [switch]$SkipSignup,
    [switch]$SkipForum,
    [string]$UserAgent = "jA-edge-check/1.0 (PowerShell; load and rate-limit test)"
)

$ErrorActionPreference = "Stop"
$Portal = $Portal.TrimEnd("/")
$Forum = $Forum.TrimEnd("/")
$script:Failures = 0

function Write-Result([bool]$Ok, [string]$Text) {
    if ($Ok) {
        Write-Host "  PASS  $Text" -ForegroundColor Green
    } else {
        Write-Host "  CHECK $Text" -ForegroundColor Yellow
        $script:Failures++
    }
}

function Get-Layer($Response) {
    # Who answered: Cloudflare itself, the nginx in front of an application,
    # or the application. A 429 means something different from each.
    if ($null -eq $Response) { return "no answer" }
    $headers = $Response.Headers
    if ($headers.ContainsKey("cf-mitigated")) { return "Cloudflare challenge" }
    $body = [string]$Response.Content
    if ($body -match "<center>cloudflare</center>") { return "Cloudflare" }
    if ($body -match "<center>nginx") { return "nginx" }
    return "application"
}

function Invoke-Probe([string]$Method, [string]$Uri, $Session, [hashtable]$Form) {
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $arguments = @{
        Method = $Method; Uri = $Uri; WebSession = $Session; UserAgent = $UserAgent
        SkipHttpErrorCheck = $true; TimeoutSec = 30
    }
    if ($Form) { $arguments.Body = $Form }
    try {
        $response = Invoke-WebRequest @arguments -ErrorAction Stop
    } catch {
        # No HTTP answer at all: a timeout, a reset, a name that did not resolve.
        return [pscustomobject]@{ Status = 0; Ms = $watch.ElapsedMilliseconds; Layer = "no answer"; Error = $_.Exception.Message }
    }
    # A form the portal refused -- an expired session, a missing cookie --
    # comes back as a redirect elsewhere, which the redirect then turns into
    # a 200 for a different page. Counted as what it is.
    $landed = $response.BaseResponse.RequestMessage.RequestUri.AbsolutePath
    if ($landed -ne ([uri]$Uri).AbsolutePath) {
        return [pscustomobject]@{ Status = 302; Ms = $watch.ElapsedMilliseconds; Layer = "sent to $landed"; Error = $null }
    }
    return [pscustomobject]@{ Status = [int]$response.StatusCode; Ms = $watch.ElapsedMilliseconds; Layer = (Get-Layer $response); Error = $null }
}

function Get-CsrfToken([string]$Uri, $Session) {
    $page = Invoke-WebRequest -Uri $Uri -WebSession $Session -UserAgent $UserAgent -SkipHttpErrorCheck -TimeoutSec 30
    if ($page.StatusCode -ne 200) {
        throw "GET $Uri answered $($page.StatusCode) ($(Get-Layer $page)); cannot read the form."
    }
    $match = [regex]::Match($page.Content, 'name="csrf_token"[^>]*value="([^"]+)"')
    if (-not $match.Success) {
        $match = [regex]::Match($page.Content, 'value="([^"]+)"[^>]*name="csrf_token"')
    }
    if (-not $match.Success) { throw "No csrf_token on $Uri." }
    return $match.Groups[1].Value
}

function Format-Statuses($Results) {
    ($Results | Group-Object Status | Sort-Object Name | ForEach-Object {
        $layers = ($_.Group | Group-Object Layer | ForEach-Object { $_.Name }) -join "/"
        "$($_.Name) x$($_.Count) ($layers)"
    }) -join ", "
}

function Get-Percentile($Values, [double]$Fraction) {
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 0) { return 0 }
    $index = [math]::Min($sorted.Count - 1, [math]::Floor($sorted.Count * $Fraction))
    return $sorted[$index]
}

$run = (Get-Random -Maximum 99999999).ToString("00000000")
Write-Host "Edge check $run"
Write-Host "  portal $Portal"
Write-Host "  forum  $Forum"

# --- 1. Many readers at once --------------------------------------------------
if (-not $SkipRead) {
    Write-Host "`n1. Portal, $ReadClients readers for $ReadSeconds s (/, /join, /login)"
    $deadline = (Get-Date).AddSeconds($ReadSeconds)
    $results = 1..$ReadClients | ForEach-Object -ThrottleLimit $ReadClients -Parallel {
        $paths = "/", "/join", "/login"
        $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $n = $_
        while ((Get-Date) -lt $using:deadline) {
            $path = $paths[$n % $paths.Count]; $n++
            $watch = [System.Diagnostics.Stopwatch]::StartNew()
            try {
                $r = Invoke-WebRequest -Uri ($using:Portal + $path) -WebSession $session -UserAgent $using:UserAgent `
                    -SkipHttpErrorCheck -TimeoutSec 30 -ErrorAction Stop
                $layer = if ($r.Headers.ContainsKey("cf-mitigated")) { "Cloudflare challenge" }
                         elseif ([string]$r.Content -match "<center>cloudflare</center>") { "Cloudflare" }
                         elseif ([string]$r.Content -match "<center>nginx") { "nginx" } else { "application" }
                [pscustomobject]@{ Status = [int]$r.StatusCode; Ms = $watch.ElapsedMilliseconds; Layer = $layer }
            } catch {
                [pscustomobject]@{ Status = 0; Ms = $watch.ElapsedMilliseconds; Layer = "no answer" }
            }
        }
    }
    $results = @($results)
    $ms = $results | ForEach-Object Ms
    $rate = [math]::Round($results.Count / $ReadSeconds, 1)
    Write-Host ("  {0} requests, {1}/s; latency p50 {2} ms, p95 {3} ms, max {4} ms" -f `
        $results.Count, $rate, (Get-Percentile $ms 0.5), (Get-Percentile $ms 0.95), ($ms | Measure-Object -Maximum).Maximum)
    Write-Host "  answers: $(Format-Statuses $results)"
    $bad = @($results | Where-Object { $_.Status -ne 200 })
    Write-Result ($bad.Count -eq 0) "every page load answered 200"
    Write-Result ((Get-Percentile $ms 0.95) -lt 2000) "p95 under 2 s"
}

# --- 2. Login limits ------------------------------------------------------------
if (-not $SkipLogin) {
    Write-Host "`n2. Portal, login limits"
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $token = Get-CsrfToken "$Portal/login" $session
    $target = "edge-check-$run-target@example.com"
    $answers = foreach ($i in 1..11) {
        Invoke-Probe POST "$Portal/login" $session @{ csrf_token = $token; email = $target; password = "wrong-$i" }
    }
    Write-Host "  one address, 11 wrong passwords: $(($answers | ForEach-Object Status) -join ' ')"
    Write-Result ((@($answers[0..9] | Where-Object Status -ne 200).Count -eq 0) -and $answers[10].Status -eq 429) `
        "the first ten are answered (200), the eleventh is refused with 429"

    $answers = foreach ($i in 1..40) {
        Invoke-Probe POST "$Portal/login" $session @{ csrf_token = $token; email = "edge-check-$run-student$i@example.com"; password = "wrong" }
    }
    Write-Host "  40 different addresses, one try each: $(Format-Statuses $answers)"
    Write-Result (@($answers | Where-Object Status -ne 200).Count -eq 0) "a lecture hall on one network is answered (all 200)"
}

# --- 3. Signup limits -----------------------------------------------------------
if (-not $SkipSignup) {
    Write-Host "`n3. Portal, signup limits (incomplete forms: nothing is stored or sent)"
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $token = Get-CsrfToken "$Portal/join" $session
    $target = "edge-check-$run-signup@example.com"
    $answers = foreach ($i in 1..11) {
        Invoke-Probe POST "$Portal/process-membership" $session @{ csrf_token = $token; email_private = $target }
    }
    Write-Host "  one address, 11 tries: $(($answers | ForEach-Object Status) -join ' ')"
    Write-Result ((@($answers[0..9] | Where-Object Status -ne 200).Count -eq 0) -and $answers[10].Status -eq 429) `
        "the first ten are answered (200), the eleventh is refused with 429"

    $answers = foreach ($i in 1..30) {
        Invoke-Probe POST "$Portal/process-membership" $session @{ csrf_token = $token; email_private = "edge-check-$run-new$i@example.com" }
    }
    Write-Host "  30 different addresses: $(Format-Statuses $answers)"
    Write-Result (@($answers | Where-Object Status -ne 200).Count -eq 0) "thirty signups from one network are answered (all 200)"
}

# --- 4. Forum, anonymous burst ----------------------------------------------------
if (-not $SkipForum) {
    Write-Host "`n4. Forum, $ForumBurst anonymous page loads as fast as possible"
    $results = @(1..$ForumBurst | ForEach-Object -ThrottleLimit 20 -Parallel {
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $r = Invoke-WebRequest -Uri ($using:Forum + "/login") -UserAgent $using:UserAgent `
                -SkipHttpErrorCheck -TimeoutSec 30 -ErrorAction Stop
            $layer = if ($r.Headers.ContainsKey("cf-mitigated")) { "Cloudflare challenge" }
                     elseif ([string]$r.Content -match "<center>cloudflare</center>") { "Cloudflare" }
                     elseif ([string]$r.Content -match "<center>nginx") { "nginx" } else { "Discourse" }
            [pscustomobject]@{ Status = [int]$r.StatusCode; Ms = $watch.ElapsedMilliseconds; Layer = $layer }
        } catch {
            [pscustomobject]@{ Status = 0; Ms = $watch.ElapsedMilliseconds; Layer = "no answer" }
        }
    })
    Write-Host "  answers: $(Format-Statuses $results)"
    $ok = @($results | Where-Object Status -eq 200).Count
    $refused = @($results | Where-Object Status -eq 429).Count
    $challenged = @($results | Where-Object Layer -eq "Cloudflare challenge").Count
    if ($challenged) {
        Write-Result $false "Cloudflare challenged $challenged requests, so this says nothing about the forum behind it; allow this user agent in Cloudflare or run it from a browser-like client"
    } else {
        Write-Result ($ok -ge 30) "the first loads are answered ($ok answered 200)"
        Write-Result ($refused -gt 0) "a flood from one address is cut off ($refused refused with 429)"
        Write-Result (@($results | Where-Object { $_.Status -ge 500 -or $_.Status -eq 0 }).Count -eq 0) "nothing failed outright (no 5xx, no timeouts)"
    }
}

Write-Host ""
if ($script:Failures) {
    Write-Host "$script:Failures things to look at." -ForegroundColor Yellow
    exit 1
}
Write-Host "Everything as expected." -ForegroundColor Green
