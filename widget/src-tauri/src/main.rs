#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::process::Child;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime};
use tauri::{LogicalSize, Manager, WebviewWindow};

const NO_WINDOW: u32 = 0x0800_0000;
const REL_SCRIPT: &str = "skills/alethe/scripts/orchestrate.py";
static OPEN_CLIS: OnceLock<Mutex<Vec<(String, Child)>>> = OnceLock::new();

fn open_clis() -> &'static Mutex<Vec<(String, Child)>> {
    OPEN_CLIS.get_or_init(|| Mutex::new(Vec::new()))
}

fn active_clis() -> Vec<String> {
    let mut children = open_clis().lock().unwrap();
    children.retain_mut(|(_, child)| child.try_wait().map_or(true, |status| status.is_none()));
    children.iter().map(|(provider, _)| provider.clone()).collect()
}

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
    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled = resource_dir.join("skill/scripts/orchestrate.py");
        if bundled.is_file() { return Some(bundled); }
    }
    [".claude", ".codex", ".agents"].iter()
        .map(|dir| home().join(dir).join("skills/alethe/scripts/orchestrate.py"))
        .find(|p| p.is_file())
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
    let clis = active_clis();
    let folder = read_json(&orch_home().join("current.json"))
        .and_then(|v| v["folder"].as_str().map(PathBuf::from));
    let Some(folder) = folder.filter(|f| f.is_dir()) else {
        return json!({"providers": clis, "runActive": false, "hasRuns": false, "last": null, "context": "CLI aberta"});
    };
    let mut states: Vec<PathBuf> = vec![folder.join("state.json")];
    if let Ok(rd) = std::fs::read_dir(&folder) {
        states.extend(rd.flatten().map(|e| e.path().join("state.json")));
    }
    states.retain(|p| p.is_file());
    states.sort();
    let mut providers: Vec<String> = clis.clone();
    let mut run_active = false;
    for p in &states {
        let Some(s) = read_json(p) else { continue };
        if s["status"] == "running" && fresh(p, 30) {
            run_active = true;
            for w in s["workers"].as_object().into_iter().flat_map(|m| m.values()) {
                if w["status"] == "running" {
                    let name = w["provider"].as_str().unwrap_or("custom").to_string();
                    if !providers.contains(&name) { providers.push(name); }
                }
            }
        }
    }
    let last = states.last().and_then(|p| read_json(p)).and_then(|s| s["status"].as_str().map(String::from));
    let meta = read_json(&folder.join("orchestration.json")).unwrap_or(Value::Null);
    let context = ["playbook", "profile", "goal"].iter().filter_map(|k| meta[*k].as_str()).collect::<Vec<_>>().join(" · ");
    json!({"providers": providers, "runActive": run_active, "hasRuns": !states.is_empty(), "last": last, "context": if !run_active && !clis.is_empty() { "CLI aberta" } else { &context }})
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

fn skill_targets(root: &Path, providers: &Value) -> Vec<PathBuf> {
    let present = |name: &str| providers.as_array().into_iter().flatten()
        .any(|p| p["provider"] == name && p["installed"] == true);
    [(".claude", present("claude")), (".codex", present("codex")),
     (".agents", present("codex") || present("agy") || present("kimi"))]
        .into_iter().filter(|(dir, detected)| *detected || root.join(dir).is_dir())
        .map(|(dir, _)| root.join(dir).join("skills/alethe")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detected_clis_get_skill_targets_even_before_config_exists() {
        let root = Path::new("nonexistent-alethe-test-home");
        let providers = json!([
            {"provider": "claude", "installed": true},
            {"provider": "codex", "installed": true},
            {"provider": "kimi", "installed": false}
        ]);
        assert_eq!(skill_targets(root, &providers), vec![
            root.join(".claude/skills/alethe"), root.join(".codex/skills/alethe"),
            root.join(".agents/skills/alethe")
        ]);
        assert!(skill_targets(root, &json!([])).is_empty());
    }
}

/// Install the skill for every agent CLI that is present, then apply profile and goal.
#[tauri::command]
fn setup(app: tauri::AppHandle, profile: String, goal: String, full: bool) -> Value {
    let mut log: Vec<String> = Vec::new();
    if !["base", "savings", "aggressive"].contains(&profile.as_str()) || !["save", "balanced", "max", "freestyle"].contains(&goal.as_str()) {
        return json!({"ok": false, "log": ["✗ Invalid profile or goal"]});
    }
    if python().is_none() {
        step(&mut log, false, "Python 3 is missing: use the Install Python button".into());
        return json!({"ok": false, "log": log});
    }
    let Some(script) = find_script(&app) else {
        step(&mut log, false, "orchestrate.py not found".into());
        return json!({"ok": false, "log": log});
    };
    let skill = script.parent().and_then(Path::parent).unwrap().to_path_buf();
    let providers = match run_py(&app, &["probe"]).and_then(|s| serde_json::from_str::<Value>(&s).map_err(|e| e.to_string())) {
        Ok(p) if p.is_array() => p,
        Ok(_) => return json!({"ok": false, "log": ["✗ Invalid CLI detection result"]}),
        Err(e) => return json!({"ok": false, "log": [format!("✗ CLI detection failed: {}", e)]}),
    };
    let targets = skill_targets(&home(), &providers);
    if targets.is_empty() {
        step(&mut log, false, "No agent CLI or skill folder found".into());
    }
    for dest in targets {
        if !full && dest.join("SKILL.md").is_file() && dest.join("scripts/orchestrate.py").is_file() && dest.join("playbooks.yaml").is_file() { continue; }
        let same = matches!((dest.canonicalize(), skill.canonicalize()), (Ok(a), Ok(b)) if a == b);
        let result = if same { Ok(()) } else { copy_dir(&skill, &dest) };
        step(&mut log, result.is_ok(), format!("skill installed in {}{}", dest.display(), result.err().map(|e| format!(": {}", e)).unwrap_or_default()));
    }
    // Always-on mode: a UserPromptSubmit hook reminds the lead on every prompt, so /alethe is not needed.
    if home().join(".claude/skills/alethe/scripts/orchestrate.py").is_file() {
        let hooked = claude_hook(&home().join(".claude/skills/alethe/scripts/orchestrate.py"));
        step(&mut log, hooked.is_ok(), format!("Claude Code hook (always on){}", hooked.err().map(|e| format!(": {}", e)).unwrap_or_default()));
    }
    let current = run_py(&app, &["playbook", "--path"]).ok().map(PathBuf::from);
    let bundled = skill.join("playbooks.yaml");
    let has_copy = current.as_ref().map_or(false, |p| p.is_file() && p.canonicalize().ok() != bundled.canonicalize().ok());
    if has_copy {
        step(&mut log, true, "playbooks copy kept".into());
    } else {
        let created = run_py(&app, &["playbook", "--init"]);
        step(&mut log, created.is_ok(), format!("playbooks copy{}", created.err().map(|e| format!(": {}", e)).unwrap_or_default()));
    }
    let mut cmd = |label: &str, args: &[&str]| {
        let r = run_py(&app, args);
        step(&mut log, r.is_ok(), format!("{}{}", label, r.err().map(|e| format!(": {}", e.lines().last().unwrap_or(""))).unwrap_or_default()));
    };
    cmd("policy saved", &["policy", "set"]);
    cmd(&format!("mode on, goal {}", goal), &["mode", "on", "--everywhere", "--goal", &goal]);
    // profile lives as `profile: <id>` in the user's playbooks copy
    let path = run_py(&app, &["playbook", "--path"]).ok().map(PathBuf::from);
    let applied = path.filter(|p| p.canonicalize().ok() != bundled.canonicalize().ok()).and_then(|p| {
        let text = std::fs::read_to_string(&p).ok()?;
        if !text.lines().any(|l| l.starts_with("profile:")) { return None; }
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
fn open_cli(provider: String, cwd: Option<String>, request: Option<String>) -> Result<(), String> {
    let dir = cwd.map(PathBuf::from).filter(|p| p.is_dir()).unwrap_or_else(home);
    let cli = match provider.as_str() { "codex" => "codex", "agy" => "agy", "kimi" => "kimi", _ => "claude" };
    // The request goes through cmd, so only plain text survives: no & | < > ^ % ! " ; or newlines.
    // Only claude and codex are known to take the first prompt as an argument.
    let prompt: Vec<String> = request
        .map(|r| r.chars().map(|c| if c.is_alphanumeric() || " .,:?-_/()'".contains(c) { c } else { ' ' }).collect::<String>())
        .map(|r| r.split_whitespace().collect::<Vec<_>>().join(" ").trim_start_matches('-').chars().take(1500).collect::<String>())
        .filter(|r| !r.is_empty() && matches!(cli, "claude" | "codex"))
        // invoke the skill explicitly instead of relying on the model noticing the hook reminder
        .map(|r| if cli == "claude" { format!("/alethe {r}") } else { format!("Use the alethe skill. {r}") })
        .into_iter().collect();
    let child = Command::new("cmd")
        .args(["/c", cli]).args(&prompt).current_dir(&dir)
        .creation_flags(0x0000_0010).spawn().map_err(|e| e.to_string())?;
    open_clis().lock().unwrap().push((cli.to_string(), child));
    Ok(())
}

/// The routing map of the current orchestration; the playbook flow when nothing has run yet.
#[tauri::command]
fn show_diagram(app: tauri::AppHandle, has_runs: bool) -> Result<String, String> {
    if has_runs {
        if let Ok(out) = run_py(&app, &["report", "--format", "html"]) { return Ok(out); }
        // no readable run (e.g. a stale pointer): show the playbook flow instead of an error
    }
    // The editor opens on its first playbook; open it on the current orchestration's instead.
    let page: Value = serde_json::from_str(&run_py(&app, &["editor", "--no-open"])?).map_err(|e| e.to_string())?;
    let path = page["path"].as_str().ok_or("editor printed no path")?.to_string();
    let playbook = read_json(&orch_home().join("current.json"))
        .and_then(|v| v["folder"].as_str().map(PathBuf::from))
        .and_then(|f| read_json(&f.join("orchestration.json")))
        .and_then(|m| m["playbook"].as_str().map(String::from));
    let mut url = format!("file:///{}", path.replace('\\', "/").replace(' ', "%20"));
    if let Some(p) = playbook { url.push_str(&format!("#playbook:{p}")); }
    quiet("cmd").args(["/c", "start", "", &url]).spawn().map_err(|e| e.to_string())?;
    Ok(path)
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
            let out = quiet("reg").args(["delete", RUN_KEY, "/v", "AletheBar", "/f"]).output().map_err(|e| e.to_string())?;
            if out.status.success() { Ok(false) } else { Err("could not remove the Run key".into()) }
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
