package com.example.hans.ai

import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
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
import java.util.regex.Pattern
import kotlin.math.max
import kotlin.math.min

class LocalHansBrain(
    private val context: Context,
    private val apiUrl: String,
    private val apiKey: String,
    private val model: String,
    private val hardwareStatusProvider: () -> Pair<Boolean, Boolean>,
    private val wideScanProvider: (suspend () -> List<String>)? = null,
    private val wideImageProvider: (suspend () -> Bitmap?)? = null,
    private val openVocabTargetSetter: ((String, RectF) -> Unit)? = null,
    private val diagnosticListener: ((String) -> Unit)? = null,
    private val onGraspConfirmed: ((String) -> Unit)? = null  // <-- Added callback
) {
    companion object {
        private const val TAG = "LocalHansBrain"
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

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

    @Volatile var activeTarget: String = "none"
    val targetList = mutableListOf<String>()
    var listMode: String = "unordered"
    val graspedObjectsHistory = mutableListOf<String>()

    @Volatile var latestDetections: List<TrackedDetection> = emptyList()

    private val conversationHistory = JSONArray()

    init {
        initSystemPrompt()
    }

    private fun initSystemPrompt() {
        val systemPrompt = """
            You are 'Hans', a tactile spatial navigation assistant for a visually impaired user.
            You guide the user's hand toward physical objects using a haptic bracelet and belt.

            CRITICAL SCOPE RESTRICTIONS:
            1. You are strictly a physical environment and navigation assistant.
            2. Answer in 1-2 concise, clear sentences. Avoid conversational filler.
            3. Available tools:
               - 'set_target': when user wants to find, locate, grab, or guide to an object.
               - 'get_visible_objects': when user asks what is directly in front of them right now.
               - 'scan_surroundings': when user asks what is around them, in the wider room, or to look around.
               - 'get_hardware_status': when user asks if the bracelet or belt is connected.
               - 'mark_grasped': when user says they grabbed or found the object.
               - 'clear_target_list': when user asks to stop searching.
            4. If the user asks for a specific target NOT in your standard list (like 'keys', 'wallet', 'pen', 'glasses'), use 'locate_open_vocabulary_target'.
        """.trimIndent()

        conversationHistory.put(JSONObject().apply {
            put("role", "system")
            put("content", systemPrompt)
        })
    }

    suspend fun processUserSpeech(userText: String): String = withContext(Dispatchers.IO) {
        val lowerText = userText.lowercase().trim()

        if (lowerText.contains("look around") || lowerText.contains("describe the room") || lowerText.contains("describe surroundings")) {
            return@withContext performMultimodalSceneDescription()
        }

        if (lowerText.startsWith("find") || lowerText.startsWith("where is") || lowerText.startsWith("locate")) {
            val potentialItem = lowerText.replace("find", "")
                .replace("where is", "")
                .replace("locate", "")
                .replace("the", "")
                .replace("my", "")
                .trim()

            if (potentialItem.isNotBlank() && matchValidTarget(potentialItem) == null) {
                return@withContext performOpenVocabularyGrounding(potentialItem)
            }
        }

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

            if (toolCalls == null || toolCalls.length() == 0) {
                val content = extractCleanText(message) ?: "Understood."
                conversationHistory.put(JSONObject().apply {
                    put("role", "assistant")
                    put("content", content)
                })
                return@withContext content
            }

            conversationHistory.put(message)

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

            val finalJson = callLlmEndpoint()
            val finalMsg = finalJson?.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
            return@withContext extractCleanText(finalMsg) ?: "Done."
        } catch (e: Exception) {
            Log.w(TAG, "LLM Gateway error: ${e.message}")
            return@withContext handleLocalFallbackCommand(userText)
        }
    }

    private suspend fun performMultimodalSceneDescription(): String {
        diagnosticListener?.invoke("Capturing wide snapshot...")
        val snapshot = wideImageProvider?.invoke()
        if (snapshot == null) {
            diagnosticListener?.invoke("Error: Snapshot was null")
            return "Camera snapshot unavailable."
        }

        diagnosticListener?.invoke("Encoding image (${snapshot.width}x${snapshot.height})...")
        val base64 = bitmapToBase64(snapshot)
        val prompt = "You are acting as the eyes of a visually impaired user. Look at this photo and describe the room concisely in 2 sentences. Mention major furniture, key objects, and walking paths."

        diagnosticListener?.invoke("Calling $model...")
        val response = callLlmVisionPrompt(base64, prompt)
        return response ?: "I was unable to describe the scene."
    }

    private suspend fun performOpenVocabularyGrounding(itemName: String): String {
        diagnosticListener?.invoke("Searching for $itemName...")
        val snapshot = wideImageProvider?.invoke() ?: return "Camera snapshot unavailable to search for $itemName."
        val base64 = bitmapToBase64(snapshot)

        val prompt = """
            Locate the '$itemName' in this image.
            If the '$itemName' is clearly visible, return ONLY its normalized 2D bounding box in JSON:
            {"found": true, "box_2d": [ymin, xmin, ymax, xmax]} where coordinates are floats between 0.0 and 1.0.
            If it is NOT visible, return: {"found": false}
        """.trimIndent()

        val rawResponse = callLlmVisionPrompt(base64, prompt) ?: return "Could not reach vision model for $itemName."

        try {
            val matcher = Pattern.compile("\\{.*\\}", Pattern.DOTALL).matcher(rawResponse)
            if (matcher.find()) {
                val jsonStr = matcher.group()
                val parsed = JSONObject(jsonStr)
                if (parsed.optBoolean("found", false)) {
                    val boxArray = parsed.getJSONArray("box_2d")
                    val ymin = boxArray.getDouble(0).toFloat()
                    val xmin = boxArray.getDouble(1).toFloat()
                    val ymax = boxArray.getDouble(2).toFloat()
                    val xmax = boxArray.getDouble(3).toFloat()

                    val targetRect = RectF(xmin, ymin, xmax, ymax)
                    activeTarget = itemName
                    targetList.clear()
                    targetList.add(itemName)

                    openVocabTargetSetter?.invoke(itemName, targetRect)
                    return "Found your $itemName. Guiding you towards it."
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed parsing open-vocabulary bounding box: ${e.message}")
        }

        return "I scanned the room, but I cannot see the $itemName right now."
    }

    private suspend fun executeLocalTool(name: String, args: JSONObject): String {
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
                    performOpenVocabularyGrounding(requested)
                }
            }

            "get_visible_objects" -> {
                val visible = latestDetections.filter { it.classId < 80 }
                if (visible.isEmpty()) {
                    "No recognized objects in the central view."
                } else {
                    val items = visible.joinToString(", ") { det ->
                        if (det.depth > 0) "${det.label} (${String.format("%.1fm", det.depth)})" else det.label
                    }
                    "In front of you: $items."
                }
            }

            "scan_surroundings" -> {
                performMultimodalSceneDescription()
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
                onGraspConfirmed?.invoke(prev)
                if (prev != "none") "Marked $prev as grasped." else "Grasp confirmed."
            }

            "clear_target_list" -> {
                activeTarget = "none"
                targetList.clear()
                "Target list cleared and navigation stopped."
            }

            else -> "Tool $name completed."
        }
    }

    private fun matchValidTarget(input: String): String? {
        val clean = input.lowercase().trim()
        if (clean in validTargetClasses) return clean

        for (det in latestDetections) {
            if (det.classId < 80 && calculateSimilarity(clean, det.label) > 0.70) {
                return det.label
            }
        }

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

    private suspend fun handleLocalFallbackCommand(text: String): String {
        val lower = text.lowercase().trim()

        return when {
            lower.contains("around") || lower.contains("surroundings") || lower.contains("room") || lower.contains("look around") -> {
                performMultimodalSceneDescription()
            }

            lower.contains("hardware") || lower.contains("bracelet") || lower.contains("belt") -> {
                val (bracelet, belt) = hardwareStatusProvider()
                "Bracelet: ${if (bracelet) "Connected" else "Disconnected"}. Belt: ${if (belt) "Connected" else "Disconnected"}."
            }

            lower.contains("what do you see") || lower.contains("what's visible") || lower.contains("in front") -> {
                val visible = latestDetections.filter { it.classId < 80 }
                if (visible.isEmpty()) {
                    "Nothing detected directly in front of you."
                } else {
                    "In front of you: ${visible.joinToString(", ") { it.label }}."
                }
            }

            lower.startsWith("find") || lower.startsWith("guide me to") || lower.startsWith("locate") -> {
                val item = lower.replace("find", "").replace("guide me to", "").replace("locate", "").replace("the", "").trim()
                val matched = matchValidTarget(item)
                if (matched != null) {
                    activeTarget = matched
                    "Looking for $matched."
                } else {
                    performOpenVocabularyGrounding(item)
                }
            }

            lower.contains("got it") || lower.contains("grasped") || lower.contains("picked it up") || lower.contains("have it") || lower.contains("found it") -> {
                val prev = activeTarget
                if (prev != "none") {
                    graspedObjectsHistory.add(prev)
                    targetList.remove(prev)
                }
                activeTarget = if (targetList.isNotEmpty() && listMode == "ordered") targetList[0] else "none"
                onGraspConfirmed?.invoke(prev)
                if (prev != "none") "Grasped $prev." else "Grasp confirmed."
            }

            lower.contains("stop") || lower.contains("cancel") -> {
                activeTarget = "none"
                targetList.clear()
                "Navigation stopped."
            }

            else -> "I am your spatial navigation assistant. I can help locate objects and guide your hands."
        }
    }

    private fun bitmapToBase64(bitmap: Bitmap, quality: Int = 75): String {
        val outputStream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, quality, outputStream)
        val byteArray = outputStream.toByteArray()
        return Base64.encodeToString(byteArray, Base64.NO_WRAP)
    }

    /**
     * Multimodal vision call with explicit diagnostic logging matching server_hans.py.
     */
    private fun callLlmVisionPrompt(base64Image: String, promptText: String): String? {
        val endpointUrl = "${apiUrl.trimEnd('/')}/v1/chat/completions"

        val contentArray = JSONArray().apply {
            put(JSONObject().apply {
                put("type", "text")
                put("text", promptText)
            })
            put(JSONObject().apply {
                put("type", "image_url")
                put("image_url", JSONObject().apply {
                    put("url", "data:image/jpeg;base64,$base64Image")
                })
            })
        }

        val messages = JSONArray().apply {
            put(JSONObject().apply {
                put("role", "user")
                put("content", contentArray)
            })
        }

        val payload = JSONObject().apply {
            put("model", model)
            put("messages", messages)
            put("max_tokens", 800)
            put("temperature", 0.2)
            put("stream", false)
            // Disable reasoning tokens on Qwen3.5 to prevent token exhaustion
            put("enable_thinking", false)
            put("chat_template_kwargs", JSONObject().apply {
                put("enable_thinking", false)
            })
        }

        Log.i(TAG, "📡 [Vision] Endpoint: $endpointUrl (Model: $model, Image: ${base64Image.length / 1024} KB)")

        val request = Request.Builder()
            .url(endpointUrl)
            .addHeader("Content-Type", "application/json")
            .addHeader("Authorization", "Bearer $apiKey")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                val responseBody = response.body?.string() ?: ""

                if (!response.isSuccessful) {
                    val errLog = "❌ HTTP ${response.code}: $responseBody"
                    Log.e(TAG, errLog)
                    diagnosticListener?.invoke("HTTP ${response.code}: check model name")
                    null
                } else {
                    Log.i(TAG, "✓ Received response from $model: ${responseBody.take(200)}...")
                    val json = JSONObject(responseBody)
                    val choice = json.optJSONArray("choices")?.optJSONObject(0)
                    val message = choice?.optJSONObject("message")
                    val result = extractCleanText(message)

                    if (result != null) {
                        diagnosticListener?.invoke("Vision reply received")
                        result
                    } else {
                        Log.w(TAG, "Could not extract text from message: $message")
                        diagnosticListener?.invoke("Empty reply from model")
                        null
                    }
                }
            }
        } catch (e: Exception) {
            val errLog = "❌ Network failure: ${e.message}"
            Log.e(TAG, errLog, e)
            diagnosticListener?.invoke("Vision failed: ${e.message}")
            null
        }
    }

    private fun callLlmEndpoint(): JSONObject? {
        val endpointUrl = "${apiUrl.trimEnd('/')}/v1/chat/completions"

        val payload = JSONObject().apply {
            put("model", model)
            put("messages", conversationHistory)
            put("tools", getAvailableTools())
            put("tool_choice", "auto")
            put("max_tokens", 500)
            put("temperature", 0.1)
            put("enable_thinking", false)
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
                    Log.e(TAG, "Gateway HTTP Error: ${response.code} -> ${response.body?.string()}")
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

    /**
     * Safely extracts text from Qwen3.5 messages, rejecting literal "null" and stripping thinking tags.
     */
    private fun extractCleanText(messageObj: JSONObject?): String? {
        if (messageObj == null) return null

        var raw: String? = null

        // 1. Check standard content
        if (messageObj.has("content") && !messageObj.isNull("content")) {
            val c = messageObj.optString("content", "").trim()
            if (c.isNotBlank() && !c.equals("null", ignoreCase = true)) {
                raw = c
            }
        }

        // 2. Check reasoning_content if content is null/empty
        if (raw == null && messageObj.has("reasoning_content") && !messageObj.isNull("reasoning_content")) {
            val r = messageObj.optString("reasoning_content", "").trim()
            if (r.isNotBlank() && !r.equals("null", ignoreCase = true)) {
                raw = r
            }
        }

        if (raw == null) return null

        // 3. Strip any residual <think> ... </think> blocks
        var cleaned = raw.replace(Regex("(?s)"), "").trim()
        
        // 4. If stripping leaves nothing (e.g. model output only thinking tokens), take the original
        if (cleaned.isBlank()) {
            cleaned.trim()
        }

        return if (cleaned.isNotBlank() && !cleaned.equals("null", ignoreCase = true)) cleaned else null
    }

    private fun getAvailableTools(): JSONArray {
        return JSONArray().apply {
            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "set_target")
                    put("description", "Sets the active physical target object to guide towards.")
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
                    put("name", "get_visible_objects")
                    put("description", "Returns objects currently visible in the narrow front camera view.")
                    put("parameters", JSONObject().apply { put("type", "object"); put("properties", JSONObject()) })
                })
            })

            put(JSONObject().apply {
                put("type", "function")
                put("function", JSONObject().apply {
                    put("name", "scan_surroundings")
                    put("description", "Uses the ultra-wide angle camera to scan and describe the room.")
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
                    put("description", "Marks the current object as grasped.")
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