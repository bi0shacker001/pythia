package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import kotlinx.serialization.json.jsonObject
import kotlin.test.*

class GameActionsTest {
    private fun answer(json: String) = Answer.Ok(BackendJson.parseToJsonElement(json).jsonObject)

    @Test fun anOkStateCanStillRefusePlay() {
        val state = primaryAction(answer("""{"primary":{"action":"play","enabled":false,"reason":"Choose how it runs."}}"""))
        assertFalse(state.enabled)
        assertEquals("Choose how it runs.", state.reason)
        assertEquals("Play", state.label)
    }

    @Test fun installUsesTheBackendsActionAndUnknownActionsNeverRun() {
        assertEquals(PrimaryAction("Install", true, null), primaryAction(answer("""{"primary":{"action":"install","enabled":true}}""")))
        assertFalse(primaryAction(answer("""{"primary":{"action":"erase","enabled":true}}""")).enabled)
        assertFalse(primaryAction(answer("{}" )).enabled)
        assertEquals("Disconnected", primaryAction(Answer.Unavailable("Disconnected")).reason)
    }

    @Test fun launchChoicesMatchProgramsToTheHostAndRunner() {
        val programs = """"candidates":[{"exe":"game.exe","kind":"windows"},{"exe":"game.sh","kind":"linux"},{"kind":"windows"}]"""
        assertEquals(listOf(LaunchOption("game.exe", "wine"), LaunchOption("game.sh", "native")),
            launchOptions(answer("""{$programs,"windows":false,"vias":["native","wine"]}""")))
        assertEquals(listOf(LaunchOption("game.exe", "native")),
            launchOptions(answer("""{$programs,"windows":true,"vias":["native","wine"]}""")))
        assertTrue(launchOptions(Answer.Unavailable("No folder")).isEmpty())
    }
}
