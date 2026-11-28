package dev.mytop.pythia.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.droidtop.shell.gamepad.ShellLoading
import dev.droidtop.shell.gamepad.ShellSwitch

/** ShellSwitch draws the shared visual; the interactive parent owns its role, state and single press action. */
@Composable
internal fun ToggleRow(label: String, on: Boolean, enabled: Boolean = true, change: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp)
        .toggleable(value = on, enabled = enabled, role = Role.Switch, onValueChange = { change() })
        .padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f))
        ShellSwitch(on)
    }
}

@Composable
internal fun LoadingStatus(label: String = "Working…") {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        ShellLoading()
        Text(label, Modifier.semantics { liveRegion = LiveRegionMode.Polite })
    }
}
