package com.example.hans.ai

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.util.Base64
import android.util.Log
import com.example.hans.ml.tracker.TrackedDetection
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.min

class LocalHansBrain(
    private val context: Context,
    private val apiUrl: String,
    private val apiKey: String,
    private val model: String,
    // Callback to inspect Bluetooth hardware states
    private val hardwareStatusProvider: () -> Pair<Boolean, Boolean> // Pair(braceletConnected, beltConnected)
) {
    companion object {
        private const val TAG = "LocalHansBrain"
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .build()

    // Valid COCO target classes (Excluding 'person')
    val validTargetClasses: Set<String> = setOf(
        "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
        "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
        "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
    )

    // State matching server SharedState
    @Volatile var activeTarget: String = "none"
    val targetList = mutableListOf<String>()
    var listMode: String = "unordered"
    var verbosity: String = "concise"

    // History of grasped items
    val graspedObjectsHistory = mutableListOf<String>()

    @Volatile var latestDetections: List<TrackedDetection> = emptyList()
    @Volatile var latestFrameBitmap: Bitmap? = null

    private val conversationHistory = JSONArray()

    init {
        initSystemPrompt()
    }

    private fun initSystemPrompt() {
        // Enforces strict scope restriction identical to server_main.py & SOUL.md
        val systemPrompt = """
            You are 'Hans', a tactile spatial navigation assistant for a visually impaired user.
            You guide the user's hand toward physical objects using a haptic bracelet and belt.

            CRITICAL SCOPE RESTRICTIONS:
            1. You are strictly a physical environment and navigation assistant.
            2. If the user asks a question completely unrelated to navigation, visible objects, or system settings (e.g. general knowledge, trivia, coding, philosophy), politely inform them that this is outside your scope as a spatial assistant.
            3. Answer in 1-2 concise, natural sentences. Avoid conversational pleasantries.
            4. You can only set targets that are recognizable physical items.
            5. Tools available:
               - 'set_target': when user wants to find, locate, grab, or guide to an object.
               - 'get_visible_objects': when user asks what is in front of them or visible.
               - 'get_hardware_status': when user asks if the bracelet or belt is connected.
               - 'mark_grasped': when user says they grabbed or found the object.
               - 'add_targets_to_list': when user gives multiple targets.
               - 'clear_target_list': when user asks to clear or stop searching.
               - 'find_specific_object': when user specifies a color or attribute (e.g., 'blue cup').
            6. Append speech rate tag if requested: [SPEED:SLOW], [SPEED:NORMAL], or [SPEED:FAST].
        """.trimIndent()

        conversationHistory.put(JSONObject().apply {
            put("role", "system")
            put("content", systemPrompt)
        })
    }

    suspend fun processUserSpeech(userText: String): String = withContext(Dispatchers.IO) {
        if (apiUrl.isBlank() || apiKey.isBlank()) {
            return@withContext handleLocalFallbackCommand(userText)
        }

        conversationHistory.put(JSONObject().apply {
            put("role", "user")
            put("content", userText)
        })

        try {
            val responseJson = callLlmEndpoint() ?: return@withContext handleLocalFallbackCommand(userText)
            val choice = responseJson.optJSONArray("choices")?.optJSONObject(0)
            val message = choice?.optJSONObject("message") ?: return@withContext handleLocalFallbackCommand(userText)

            val toolCalls = message.optJSONArray("tool_calls")

            // Direct verbal answer without tools
            if (toolCalls == null || toolCalls.length() == 0) {
                val content = message.optString("content", "Understood.")
                conversationHistory.put(JSONObject().apply {
                    put("role", "assistant")
                    put("content", content)
                })
                return@withContext content
            }

            conversationHistory.put(message)

            // Execute requested tools
            for (i in 0 until toolCalls.length()) {
                val call = toolCalls.getJSONObject(i)
                val callId = call.getString("id")
                val function = call.getJSONObject("function")
                val name = function.getString("name")
                val args = JSONObject(function.optString("arguments", "{}"))

                val toolResult = executeLocalTool(name, args)
                Log.d(TAG, "Tool '$name' executed -> $toolResult")

                conversationHistory.put(JSONObject().apply {
                    put("role", "tool")
                    put("tool_call_id", callId)
                    put("content", toolResult)
                })
            }

            // Follow-up LLM response after tool execution
            val finalJson = callLlmEndpoint()
            val finalContent = finalJson?.optJSONArray("choices")?.optJSONObject(0)
                ?.optJSONObject("message")?.optString("content", "Done.") ?: "Done."

            return@withContext finalContent
        } catch (e: Exception) {
            Log.w(TAG, "LLM Gateway unavailable, using local rules: ${e.message}")
            return@withContext handleLocalFallbackCommand(userText)
        }
    }

    // =================================================================
    // LOCAL TOOL DISPATCH (Exact Port of server_hans.py)
    // =================================================================
    private fun executeLocalTool(name: String, args: JSONObject): String {
        return when (name) {
            "set_target" -> {
                val requested = args.optString("target_name", "").lowercase().trim()
                val matched = matchValidTarget(requested)

                if (matched != null) {
                    activeTarget = matched
                    targetList.clear()
                    targetList.add(matched)
                    listMode = "unordered"
                    "Target successfully set to $matched."
                } else {
                    "'$requested' is not a recognized object class. Available target types include cup, bottle, chair, keyboard, laptop, and phone."
                }
            }

            "add_targets_to_list" -> {
                val targetsArray = args.optJSONArray("targets") ?: JSONArray()
                val mode = args.optString("mode", "unordered")
                val accepted = mutableListOf<String>()
                val rejected = mutableListOf<String>()

                for (i in 0 until targetsArray.length()) {
                    val raw = targetsArray.getString(i).lowercase().trim()
                    val matched = matchValidTarget(raw)
                    if (matched != null) {
                        accepted.add(matched)
                    } else {
                        rejected.add(raw)
                    }
                }

                if (accepted.isNotEmpty()) {
                    targetList.clear()
                    targetList.addAll(accepted)
                    listMode = mode
                    activeTarget = accepted[0]
                    var reply = "Target list updated with: ${accepted.joinToString(", ")}."
                    if (rejected.isNotEmpty()) {
                        reply += " Rejected unrecognized items: ${rejected.joinToString(", ")}."
                    }
                    reply
                } else {
                    "None of the requested items are recognized objects. Valid examples: cup, bottle, chair, mouse."
                }
            }

            "get_visible_objects" -> {
                val visible = latestDetections.filter { it.classId < 80 } // Objects only (exclude hands)
                if (visible.isEmpty()) {
                    "No recognized objects currently detected."
                } else {
                    val items = visible.joinToString("\n") { det ->
                        val depthStr = if (det.depth > 0) String.format("%.2fm away", det.depth) else "unknown distance"
                        "- ${det.label} (ID: ${det.trackId}, $depthStr)"
                    }
                    "Visible objects:\n$items"
                }
            }

            "get_hardware_status" -> {
                val (bracelet, belt) = hardwareStatusProvider()
                val braceletStr = if (bracelet) "Connected" else "Disconnected"
                val beltStr = if (belt) "Connected" else "Disconnected"
                "Hardware Status: Bracelet is $braceletStr. Belt is $beltStr."
            }

            "mark_grasped" -> {
                val prev = activeTarget
                if (prev != "none") {
                    graspedObjectsHistory.add(prev)
                    targetList.remove(prev)
                }
                activeTarget = if (targetList.isNotEmpty() && listMode == "ordered") targetList[0] else "none"
                "Object marked as grasped. ${if (activeTarget != "none") "Next target: $activeTarget." else "Navigation complete."}"
            }

            "get_current_target_list" -> {
                if (targetList.isEmpty()) {
                    "Your target list is currently empty."
                } else {
                    "Target list ($listMode): ${targetList.joinToString(", ")}. Current active: $activeTarget."
                }
            }

            "get_grasped_objects_history" -> {
                if (graspedObjectsHistory.isEmpty()) {
                    "No objects grasped yet."
                } else {
                    "Previously grasped objects: ${graspedObjectsHistory.joinToString(", ")}."
                }
            }

            "clear_target_list" -> {
                activeTarget = "none"
                targetList.clear()
                "Target list cleared and navigation stopped."
            }

            "find_specific_object" -> {
                val className = args.optString("class_name", "").lowercase().trim()
                val color = args.optString("description", "").lowercase().trim()

                val matchedClass = matchValidTarget(className)
                if (matchedClass == null) {
                    "Cannot search for '$className' as it is not a recognized object."
                } else {
                    val candidates = latestDetections.filter { it.label == matchedClass }
                    if (candidates.isEmpty()) {
                        "I don't see any ${matchedClass}s right now."
                    } else {
                        // Lock onto target
                        activeTarget = matchedClass
                        "Locked onto the $color $matchedClass."
                    }
                }
            }

            else -> "Tool $name executed."
        }
    }

    // =================================================================
    // COCO TARGET VALIDATION & FUZZY MATCHING (Levenshtein + Sequence)
    // =================================================================
    private fun matchValidTarget(input: String): String? {
        val clean = input.lowercase().trim()

        // 1. Exact match against valid COCO classes
        if (clean in validTargetClasses) return clean

        // 2. Check currently visible objects first (Prioritize what's right in front of the camera)
        for (det in latestDetections) {
            if (det.classId < 80 && calculateSimilarity(clean, det.label) > 0.70) {
                return det.label
            }
        }

        // 3. Fuzzy match against all COCO classes (Handles speech errors like 'cop' -> 'cup')
        var bestMatch: String? = null
        var bestScore = 0.0

        for (valid in validTargetClasses) {
            val score = calculateSimilarity(clean, valid)
            if (score > bestScore && score >= 0.75) {
                bestScore = score
                bestMatch = valid
            }
        }

        return bestMatch
    }

    private fun calculateSimilarity(s1: String, s2: String): Double {
        if (s1 == s2) return 1.0
        val dist = levenshteinDistance(s1, s2)
        val maxLen = max(s1.length, s2.length)
        return if (maxLen == 0) 1.0 else 1.0 - (dist.toDouble() / maxLen)
    }

    private fun levenshteinDistance(s1: String, s2: String): Int {
        val dp = Array(s1.length + 1) { IntArray(s2.length + 1) }
        for (i in 0..s1.length) dp[i][0] = i
        for (j in 0..s2.length) dp[0][j] = j

        for (i in 1..s1.length) {
            for (j in 1..s2.length) {
                val cost = if (s1[i - 1] == s2[j - 1]) 0 else 1
                dp[i][j] = min(dp[i - 1][j] + 1, min(dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost))
            }
        }
        return dp[s1.length][s2.length]
    }

    // =================================================================
    // OFFLINE FALLBACK
    // =================================================================
    private fun handleLocalFallbackCommand(text: String): String {
        val lower = text.lowercase().trim()

        return when {
            lower.contains("hardware") || lower.contains("bracelet") || lower.contains("belt") -> {
                val (bracelet, belt) = hardwareStatusProvider()
                "Bracelet: ${if (bracelet) "Connected" else "Disconnected"}. Belt: ${if (belt) "Connected" else "Disconnected"}."
            }

            lower.contains("what do you see") || lower.contains("what's visible") || lower.contains("what is around") -> {
                val visible = latestDetections.filter { it.classId < 80 }
                if (visible.isEmpty()) {
                    "No recognized objects visible."
                } else {
                    "I see: ${visible.joinToString(", ") { it.label }}."
                }
            }

            lower.startsWith("find") || lower.startsWith("guide me to") || lower.startsWith("locate") -> {
                val item = lower.replace("find", "").replace("guide me to", "").replace("locate", "").replace("the", "").trim()
                val matched = matchValidTarget(item)
                if (matched != null) {
                    activeTarget = matched
                    "Looking for $matched."
                } else {
                    "'$item' is not a recognized object class."
                }
            }

            lower.contains("got it") || lower.contains("grasped") -> {
                activeTarget = "none"
                "Object marked as grasped."
            }

            lower.contains("stop") || lower.contains("cancel") -> {
                activeTarget = "none"
                targetList.clear()
                "Navigation stopped."
            }

            else -> "I am your spatial navigation assistant. I can help locate objects and guide your hands."
        }
    }

    private fun callLlmEndpoint(): JSONObject? {
        val endpointUrl = "${apiUrl.trimEnd('/')}/v1/chat/completions"

        val payload = JSONObject().apply {
            put("model", model)
            put("messages", conversationHistory)
            put("tools", getAvailableTools())
            put("tool_choice", "auto")
            put("max_tokens", 350)
            put("temperature", 0.1) // Low temperature for deterministic tool calling
        }

        val request = Request.Builder()
            .url(endpointUrl)
            .addHeader("Content-Type", "application/json")
            .addHeader("Authorization", "Bearer $apiKey")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    Log.e(TAG, "Gateway HTTP Error: ${response.code}")
                    null
                } else {
                    val body = response.body?.string() ?: return null
                    JSONObject(body)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Gateway call error: ${e.message}")
            null
        }
    }

    private fun getAvailableTools(): JSONArray {
        return JSONArray().apply {
            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "set_target")
                    put("description", "Sets the active physical target object. Must be a valid physical object class.")
                    put("parameters", JSONObject().apply {
                        put("type", "object")
                        put("properties", JSONObject().apply {
                            put("target_name", JSONObject().apply {
                                put("type", "string")
                                put("description", "Name of the target object (e.g. cup, bottle, chair)")
                            })
                        })
                        put("required", JSONArray().apply { put("target_name") })
                    })
                })
            })

            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "add_targets_to_list")
                    put("description", "Adds multiple objects to search for in ordered or unordered mode.")
                    put("parameters", JSONObject().apply {
                        put("type", "object")
                        put("properties", JSONObject().apply {
                            put("targets", JSONObject().apply {
                                put("type", "array")
                                put("items", JSONObject().apply { put("type", "string") })
                            })
                            put("mode", JSONObject().apply {
                                put("type", "string")
                                put("enum", JSONArray().apply { put("ordered"); put("unordered") })
                            })
                        })
                        put("required", JSONArray().apply { put("targets") })
                    })
                })
            })

            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "get_visible_objects")
                    put("description", "Returns the list of physical objects currently detected by the camera with distances.")
                    put("parameters", JSONObject().apply { put("type", "object"); put("properties", JSONObject()) })
                })
            })

            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "get_hardware_status")
                    put("description", "Checks whether the tactile bracelet and belt are connected via Bluetooth.")
                    put("parameters", JSONObject().apply { put("type", "object"); put("properties", JSONObject()) })
                })
            })

            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "mark_grasped")
                    put("description", "Marks the current object as grasped and advances the target list.")
                    put("parameters", JSONObject().apply { put("type", "object"); put("properties", JSONObject()) })
                })
            })

            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "clear_target_list")
                    put("description", "Clears all active targets and stops haptic guidance.")
                    put("parameters", JSONObject().apply { put("type", "object"); put("properties", JSONObject()) })
                })
            })
        }
    }
}
