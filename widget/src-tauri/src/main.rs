#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime};
use tauri::{LogicalSize, Manager, WebviewWindow};

const NO_WINDOW: u32 = 0x0800_0000;
const REL_SCRIPT: &str = "skills/alethe/scripts/orchestrate.py";

fn env(name: &str) -> Option<PathBuf> {
    std::env::var_os(name).map(PathBuf::from)
}

fn home() -> PathBuf {
    env("USERPROFILE").unwrap_or_default()
}

fn quiet(program: &str) -> Command {
    let mut c = Command::new(program);
    c.creation_flags(NO_WINDOW).env("PYTHONIOENCODING", "utf-8");
    c
}

/// Same lookup as orchestrate.py: ALETHE_ORCHESTRATE_HOME, else <policy dir>/orchestrations.
fn orch_home() -> PathBuf {
    if let Some(p) = env("ALETHE_ORCHESTRATE_HOME") {
        return p;
    }
    if let Some(p) = env("ALETHE_ORCHESTRATE_POLICY") {
        return p.parent().map(|d| d.join("orchestrations")).unwrap_or_default();
    }
    env("APPDATA").unwrap_or_else(|| home().join("AppData/Roaming")).join("Alethe/orchestrations")
}

/// Python 3 launcher that really runs (the Store alias for `python` does not).
fn python() -> Option<(&'static str, Vec<&'static str>)> {
    for (cmd, pre) in [("py", vec!["-3"]), ("python", vec![]), ("python3", vec![])] {
        let out = quiet(cmd).args(&pre).arg("--version").output();
        if matches!(out, Ok(o) if o.status.success() && String::from_utf8_lossy(&o.stdout).starts_with("Python 3")) {
            return Some((cmd, pre));
        }
    }
    None
}

fn find_script(app: &tauri::AppHandle) -> Option<PathBuf> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.extend(exe.parent().map(Path::to_path_buf));
    }
    starts.extend(std::env::current_dir().ok());
    for s in starts {
        for a in s.ancestors() {
            let p = a.join(REL_SCRIPT);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    let bundled = app.path().resource_dir().ok()?.join("skill/scripts/orchestrate.py");
    if bundled.is_file() { return Some(bundled); }
    let installed = home().join(".claude/skills/alethe/scripts/orchestrate.py");
    installed.is_file().then_some(installed)
}

fn run_py(app: &tauri::AppHandle, args: &[&str]) -> Result<String, String> {
    let (cmd, pre) = python().ok_or("Python 3 not found")?;
    let script = find_script(app).ok_or("orchestrate.py not found")?;
    let out = quiet(cmd).args(pre).arg(script).args(args).output().map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if out.status.success() {
        Ok(text)
    } else {
        Err(format!("{}{}", text, String::from_utf8_lossy(&out.stderr)).trim().to_string())
    }
}

fn read_json(p: &Path) -> Option<Value> {
    serde_json::from_slice(&std::fs::read(p).ok()?).ok()
}

fn fresh(p: &Path, secs: u64) -> bool {
    std::fs::metadata(p)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|m| SystemTime::now().duration_since(m).ok())
        .map_or(false, |d| d < Duration::from_secs(secs))
}

/// Providers with a running worker. state.json is rewritten every ~5 s while a batch runs,
/// so a stale mtime means the runner died.
#[tauri::command]
fn bar_state() -> Value {
    let folder = read_json(&orch_home().join("current.json"))
        .and_then(|v| v["folder"].as_str().map(PathBuf::from));
    let Some(folder) = folder.filter(|f| f.is_dir()) else {
        return json!({"providers": [], "hasRuns": false, "last": null});
    };
    let mut states: Vec<PathBuf> = vec![folder.join("state.json")];
    if let Ok(rd) = std::fs::read_dir(&folder) {
        states.extend(rd.flatten().map(|e| e.path().join("state.json")));
    }
    states.retain(|p| p.is_file());
    states.sort();
    let mut providers: Vec<String> = Vec::new();
    for p in &states {
        let Some(s) = read_json(p) else { continue };
        if s["status"] == "running" && fresh(p, 30) {
            for w in s["workers"].as_object().into_iter().flat_map(|m| m.values()) {
                if w["status"] == "running" {
                    let name = w["provider"].as_str().unwrap_or("custom").to_string();
                    if !providers.contains(&name) { providers.push(name); }
                }
            }
        }
    }
    let last = states.last().and_then(|p| read_json(p)).and_then(|s| s["status"].as_str().map(String::from));
    json!({"providers": providers, "hasRuns": !states.is_empty(), "last": last})
}

/// Adds the alethe hook to ~/.claude/settings.json once, keeping everything else.
fn claude_hook(script: &Path) -> Result<(), String> {
    let (py, pre) = python().ok_or("Python 3 not found")?;
    let path = home().join(".claude/settings.json");
    let mut cfg = if path.is_file() { read_json(&path).ok_or("settings.json is not valid JSON")? } else { json!({}) };
    let list = cfg["hooks"]["UserPromptSubmit"].take();
    let mut list = match list { Value::Null => Vec::new(), Value::Array(a) => a, _ => return Err("UserPromptSubmit in settings.json is not a list".into()) };
    if !list.iter().any(|e| e.to_string().contains("orchestrate.py") && e.to_string().contains(" hook")) {
        let command = format!("{} {} \"{}\" hook 2>/dev/null || true", py, pre.join(" "), script.to_string_lossy().replace('\\', "/"));
        list.push(json!({"hooks": [{"type": "command", "command": command, "timeout": 10}]}));
    }
    cfg["hooks"]["UserPromptSubmit"] = Value::Array(list);
    if path.is_file() { std::fs::copy(&path, path.with_extension("json.bak-alethe")).map_err(|e| format!("backup failed: {}", e))?; }
    std::fs::write(&path, serde_json::to_string_pretty(&cfg).unwrap()).map_err(|e| e.to_string())
}

#[tauri::command]
fn doctor(app: tauri::AppHandle) -> Value {
    match run_py(&app, &["doctor"]) {
        Ok(t) => serde_json::from_str(&t).unwrap_or_else(|_| json!({"raw": t})),
        Err(e) => json!({"error": e}),
    }
}

fn copy_dir(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(to)?;
    for e in std::fs::read_dir(from)? {
        let e = e?;
        let dest = to.join(e.file_name());
        if e.file_name() == "__pycache__" { continue; }
        if e.file_type()?.is_dir() { copy_dir(&e.path(), &dest)?; } else { std::fs::copy(e.path(), dest)?; }
    }
    Ok(())
}

fn step(log: &mut Vec<String>, ok: bool, msg: String) {
    log.push(format!("{} {}", if ok { "✓" } else { "✗" }, msg));
}

/// Install the skill for every agent CLI that is present, then apply profile and goal.
#[tauri::command]
fn setup(app: tauri::AppHandle, profile: String, goal: String, full: bool) -> Value {
    let mut log: Vec<String> = Vec::new();
    if python().is_none() {
        step(&mut log, false, "Python 3 is missing: use the Install Python button".into());
        return json!({"ok": false, "log": log});
    }
    let Some(script) = find_script(&app) else {
        step(&mut log, false, "orchestrate.py not found".into());
        return json!({"ok": false, "log": log});
    };
    let skill = script.parent().and_then(Path::parent).unwrap().to_path_buf();
    if full { // install/update only on the first run or on request; applying a choice skips this
    for agent in [".claude", ".codex", ".agents"] {
        let base = home().join(agent);
        if !base.is_dir() { continue; }
        let dest = base.join("skills/alethe");
        let same = dest.canonicalize().ok() == skill.canonicalize().ok();
        let ok = same || { let _ = std::fs::remove_dir_all(&dest); copy_dir(&skill, &dest).is_ok() };
        step(&mut log, ok, format!("skill installed in {}", dest.display()));
    }
    // Always-on mode: a UserPromptSubmit hook reminds the lead on every prompt, so /alethe is not needed.
    let hooked = claude_hook(&home().join(".claude/skills/alethe/scripts/orchestrate.py"));
    step(&mut log, hooked.is_ok(), format!("Claude Code hook (always on){}", hooked.err().map(|e| format!(": {}", e)).unwrap_or_default()));
    let created = run_py(&app, &["playbook", "--init"]).is_ok();
    step(&mut log, true, if created { "playbooks copy created".into() } else { "playbooks copy kept (already exists)".into() });
    }
    let mut cmd = |label: &str, args: &[&str]| {
        let r = run_py(&app, args);
        step(&mut log, r.is_ok(), format!("{}{}", label, r.err().map(|e| format!(": {}", e.lines().last().unwrap_or(""))).unwrap_or_default()));
    };
    cmd("policy saved", &["policy", "set"]);
    cmd(&format!("mode on, goal {}", goal), &["mode", "on", "--everywhere", "--goal", &goal]);
    // profile lives as `profile: <id>` in the user's playbooks copy
    let path = run_py(&app, &["playbook", "--path"]).ok().map(PathBuf::from);
    let applied = path.filter(|p| p.file_name().map_or(false, |n| n == "alethe-playbooks.yaml")).and_then(|p| {
        let text = std::fs::read_to_string(&p).ok()?;
        let out: Vec<String> = text.lines().map(|l| if l.starts_with("profile:") { format!("profile: {}", profile) } else { l.to_string() }).collect();
        std::fs::write(&p, out.join("\n") + "\n").ok()
    });
    step(&mut log, applied.is_some(), format!("profile {}", profile));
    let ok = log.iter().all(|l| l.starts_with('✓'));
    json!({"ok": ok, "log": log})
}

/// True once the skill is installed for at least one agent CLI.
/// Folders where Claude Code already ran (~/.claude.json), newest first.
#[tauri::command]
fn known_projects() -> Vec<String> {
    let cfg = read_json(&home().join(".claude.json")).unwrap_or(Value::Null);
    let mut dirs: Vec<(SystemTime, String)> = cfg["projects"].as_object().into_iter().flatten()
        .filter(|(k, _)| Path::new(k.as_str()).is_dir())
        .map(|(k, _)| (std::fs::metadata(k).and_then(|m| m.modified()).unwrap_or(SystemTime::UNIX_EPOCH), k.clone()))
        .collect();
    dirs.sort_by(|a, b| b.0.cmp(&a.0));
    dirs.into_iter().take(15).map(|d| d.1).collect()
}

/// Windows folder picker, through PowerShell so no extra crate is needed.
#[tauri::command]
fn pick_folder() -> Option<String> {
    let script = "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.ShowNewFolderButton = $false; if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }";
    let out = quiet("powershell").args(["-NoProfile", "-STA", "-Command", script]).output().ok()?;
    let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (!path.is_empty()).then_some(path)
}

#[tauri::command]
fn installed() -> bool {
    [".claude", ".codex", ".agents"].iter().any(|a| home().join(a).join("skills/alethe/SKILL.md").is_file())
}

#[tauri::command]
fn install_python() -> Result<(), String> {
    Command::new("winget")
        .args(["install", "-e", "--id", "Python.Python.3.12", "--accept-package-agreements", "--accept-source-agreements"])
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Opens the agent CLI in a terminal, where the user types the request.
#[tauri::command]
fn open_cli(provider: String, cwd: Option<String>) -> Result<(), String> {
    let dir = cwd.map(PathBuf::from).filter(|p| p.is_dir()).unwrap_or_else(home);
    let cli = match provider.as_str() { "codex" => "codex", "agy" => "agy", "kimi" => "kimi", _ => "claude" };
    let wt = Command::new("cmd")
        .args(["/c", "start", "", "wt.exe", "-d"]).arg(&dir).args(["--title", "Alethe", "cmd", "/k", cli])
        .creation_flags(NO_WINDOW).spawn();
    if wt.is_err() {
        Command::new("cmd").args(["/c", "start", "", "cmd", "/k", cli]).current_dir(&dir)
            .creation_flags(NO_WINDOW).spawn().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// The routing map of the current orchestration; the playbook flow when nothing has run yet.
#[tauri::command]
fn show_diagram(app: tauri::AppHandle, has_runs: bool) -> Result<String, String> {
    run_py(&app, if has_runs { &["report", "--format", "html"] } else { &["editor"] })
}

const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";

/// Start with Windows: a per-user Run entry, no admin rights needed.
#[tauri::command]
fn autostart(enable: Option<bool>) -> Result<bool, String> {
    match enable {
        None => Ok(quiet("reg").args(["query", RUN_KEY, "/v", "AletheBar"]).output().map_or(false, |o| o.status.success())),
        Some(true) => {
            let exe = std::env::current_exe().map_err(|e| e.to_string())?;
            let value = format!("\"{}\"", exe.display());
            let ok = quiet("reg").args(["add", RUN_KEY, "/v", "AletheBar", "/t", "REG_SZ", "/d", &value, "/f"]).output().map_or(false, |o| o.status.success());
            if ok { Ok(true) } else { Err("could not write the Run key".into()) }
        }
        Some(false) => {
            let _ = quiet("reg").args(["delete", RUN_KEY, "/v", "AletheBar", "/f"]).output();
            Ok(false)
        }
    }
}

const REPO: &str = "Kc1t/alethe-orchestrate-skill";

fn version_parts(v: &str) -> Vec<u32> {
    v.trim_start_matches(|c: char| !c.is_ascii_digit()).split('.').map(|n| n.parse().unwrap_or(0)).collect()
}

/// Newest `bar-v*` release on GitHub (curl ships with Windows 10+; no extra crate).
#[tauri::command]
fn check_update() -> Value {
    let current = env!("CARGO_PKG_VERSION");
    let url = format!("https://api.github.com/repos/{}/releases?per_page=20", REPO);
    let out = quiet("curl.exe").args(["-fsSL", "--max-time", "8", "-H", "User-Agent: alethe-bar", &url]).output();
    let list: Value = out.ok().and_then(|o| serde_json::from_slice(&o.stdout).ok()).unwrap_or(Value::Null);
    let best = list.as_array().into_iter().flatten()
        .filter(|r| r["draft"] != true && r["prerelease"] != true)
        .filter_map(|r| Some((r["tag_name"].as_str()?.strip_prefix("bar-v")?.to_string(), r["html_url"].as_str()?.to_string())))
        .max_by_key(|(v, _)| version_parts(v));
    match best {
        Some((v, u)) if version_parts(&v) > version_parts(current) && u.starts_with("https://github.com/") =>
            json!({"newer": true, "current": current, "latest": v, "url": u}),
        _ => json!({"newer": false, "current": current}),
    }
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !url.starts_with("https://github.com/") { return Err("blocked".into()); }
    Command::new("cmd").args(["/c", "start", "", &url]).creation_flags(NO_WINDOW).spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn resize(w: WebviewWindow, height: f64) -> Result<(), String> {
    w.set_size(LogicalSize::new(600.0, height)).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![bar_state, doctor, setup, installed, known_projects, pick_folder, install_python, open_cli, show_diagram, resize, check_update, open_url, autostart])
        .run(tauri::generate_context!())
        .expect("failed to run Alethe Bar");
}
