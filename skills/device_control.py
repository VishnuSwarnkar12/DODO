"""
DODO — skills/device_control.py
Bluetooth and WiFi control via PowerShell/netsh on Windows.
"""

from skills.utils import run_powershell, run_command


def set_bluetooth(enable: bool) -> bool:
    """Toggle Bluetooth using Windows Radio Management API via PowerShell."""
    state = "true" if enable else "false"
    script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }}
$radioMgr = [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]
$op = $radioMgr::RequestAccessAsync()
$task = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime]).Invoke($null, @($op))
$task.Wait()
$radios = $radioMgr::GetRadiosAsync()
$task2 = $asTask.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio],Windows.System.Devices,ContentType=WindowsRuntime]).Invoke($null, @($radios))
$task2.Wait()
foreach ($r in $task2.Result) {{
    if ($r.Kind -eq 'Bluetooth') {{
        $state = if ({state}) {{ [Windows.Devices.Radios.RadioState]::On }} else {{ [Windows.Devices.Radios.RadioState]::Off }}
        $op3 = $r.SetStateAsync($state)
        $task3 = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime]).Invoke($null, @($op3))
        $task3.Wait()
    }}
}}
"""
    code, _, err = run_powershell(script)
    return code == 0


def set_wifi(enable: bool) -> bool:
    """Toggle WiFi using netsh."""
    action = "enable" if enable else "disable"
    code, _, _ = run_command(
        f'netsh interface set interface "Wi-Fi" admin={action}', shell=True
    )
    return code == 0


def get_wifi_status() -> str:
    code, out, _ = run_command(
        'netsh interface show interface "Wi-Fi"', shell=True
    )
    if "Enabled" in out:
        return "on"
    return "off"
