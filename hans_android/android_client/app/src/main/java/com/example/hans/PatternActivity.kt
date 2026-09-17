package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.Button

import androidx.appcompat.app.AppCompatActivity
import android.widget.RelativeLayout
import android.util.Log
import org.json.JSONObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

class PatternActivity : AppCompatActivity() {

    private val PATTERN_PREFS = "PatternPrefs"
    private var selectedIndex = 0
    private lateinit var VIB_PATTERN_SINGLE: RelativeLayout
    private lateinit var VIB_PATTERN_MULTI: RelativeLayout
    private lateinit var VIB_PATTERN_SEQ: RelativeLayout
    private lateinit var braceletManager: BleManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pattern)

        braceletManager = BleManagerSingleton.getBraceletManager(this)

        VIB_PATTERN_SINGLE = findViewById(R.id.button_single)
        VIB_PATTERN_MULTI = findViewById(R.id.button_multi)
        VIB_PATTERN_SEQ = findViewById(R.id.button_sequence)

        val selectButton = findViewById<Button>(R.id.button_select_pattern)

        val prefs = getSharedPreferences(PATTERN_PREFS, MODE_PRIVATE)

        // Load pattern that is saved
        selectedIndex = prefs.getInt("PATTERN_INDEX", 0)

        updateSelection()

        // Single Pattern
        VIB_PATTERN_SINGLE.setOnClickListener {
            selectedIndex = 0
            updateSelection()
            previewPattern(0x00)
        }

        // Multimotor Continuous Pattern
        VIB_PATTERN_MULTI.setOnClickListener {
            selectedIndex = 1
            updateSelection()
            previewPattern(0x01)
        }

        // Multimotor Sequence Pattern
        VIB_PATTERN_SEQ.setOnClickListener {
            selectedIndex = 2
            updateSelection()
            previewPattern(0x02)
        }

        // Save option
        selectButton.setOnClickListener {

            val patternCode = when (selectedIndex) {
                0 -> "VIB_PATTERN_SINGLE"
                1 -> "VIB_PATTERN_MULTI"
                2 -> "VIB_PATTERN_SEQ"
                else -> "VIB_PATTERN_SINGLE"
            }

            prefs.edit()
                .putInt("PATTERN_INDEX", selectedIndex)
                .putString("PATTERN_CODE", patternCode)
                .apply()

            syncPreferencesToServer(patternCode)
            stopPatternPreview()
            finish()
        }

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNavigation)

        bottomNav.selectedItemId = R.id.menu_setting
        bottomNav.setOnItemSelectedListener { item ->

            when (item.itemId) {

                R.id.menu_home -> {
                    stopPatternPreview()
                    startActivity(Intent(this, BluetoothActivity::class.java))
                    finish()
                    true
                }

                R.id.menu_camera -> {
                    stopPatternPreview()
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                    true
                }

                R.id.menu_setting -> {
                    stopPatternPreview()
                    startActivity(Intent(this, SettingsActivity::class.java))
                    finish()
                    true
                }

                else -> false
            }
        }
    }

    private fun syncPreferencesToServer(patternCode: String) {
        val serverIp = BuildConfig.SERVER_IP
        val url = "http://$serverIp:8000/api/preferences"

        val intensityPrefs = getSharedPreferences("FullIntensityPrefs", MODE_PRIVATE)

        val json = JSONObject().apply {
            put("text", "")
            put("bracelet_connected", true)
            put("belt_connected", true)

            put("vibration", JSONObject().apply {
                put("left", intensityPrefs.getInt("leftIntensity", 50))
                put("bottom", intensityPrefs.getInt("bottomIntensity", 50))
                put("right", intensityPrefs.getInt("rightIntensity", 50))
                put("top", intensityPrefs.getInt("topIntensity", 50))
                put("top_front", intensityPrefs.getInt("topFrontIntensity", 50))
                put("top_back", intensityPrefs.getInt("topBackIntensity", 50))
                put("belt", intensityPrefs.getInt("beltIntensity", 50))
            })

            put("pattern", patternCode)
        }

        val body = json.toString()
            .toRequestBody("application/json; charset=utf-8".toMediaType())

        val request = Request.Builder()
            .url(url)
            .post(body)
            .build()

        OkHttpClient().newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.e("PatternActivity", "Failed to sync preferences", e)
            }

            override fun onResponse(call: Call, response: Response) {
                response.close()

                if (response.isSuccessful) {
                    Log.d("PatternActivity", "Pattern synced successfully")
                } else {
                    Log.e(
                        "PatternActivity",
                        "Pattern sync failed: HTTP ${response.code}"
                    )
                }
            }
        })
    }

    private fun previewPattern(pattern: Int) {
        stopPatternPreview()

        android.os.Handler(mainLooper).postDelayed({
            val intensityPrefs =
                getSharedPreferences("FullIntensityPrefs", MODE_PRIVATE)

            val left = intensityPrefs.getInt("leftIntensity", 50)
            val down = intensityPrefs.getInt("bottomIntensity", 50)
            val right = intensityPrefs.getInt("rightIntensity", 50)
            val top = intensityPrefs.getInt("topIntensity", 50)
            val topFront = intensityPrefs.getInt("topFrontIntensity", 50)
            val topBack = intensityPrefs.getInt("topBackIntensity", 50)

            val command = byteArrayOf(
                0x0F.toByte(),
                0x07.toByte(),
                pattern.toByte(),
                left.toByte(),
                down.toByte(),
                right.toByte(),
                top.toByte(),
                topFront.toByte(),
                topBack.toByte()
            )

            if (braceletManager.isConnected()) {
                braceletManager.writeRawCommand(command)

                Log.d(
                    "PatternActivity",
                    "Pattern preview sent: " +
                            command.joinToString("") { "%02X".format(it) }
                )
            }
        }, 200)
    }

    private fun stopPatternPreview() {
        val stopCommand = byteArrayOf(
            0x03.toByte(),
            0x01.toByte(),
            0xFF.toByte()
        )

        if (braceletManager.isConnected()) {
            braceletManager.writeRawCommand(stopCommand)

            Log.d(
                "PatternActivity",
                "Pattern preview stopped"
            )
        }
    }

    override fun onBackPressed() {
        stopPatternPreview()
        super.onBackPressed()
    }

    private fun updateSelection() {
        VIB_PATTERN_SINGLE.setBackgroundResource(R.drawable.bg_outline)
        VIB_PATTERN_MULTI.setBackgroundResource(R.drawable.bg_outline)
        VIB_PATTERN_SEQ.setBackgroundResource(R.drawable.bg_outline)

        when (selectedIndex) {
            0 -> VIB_PATTERN_SINGLE.setBackgroundResource(R.drawable.bg_green)
            1 -> VIB_PATTERN_MULTI.setBackgroundResource(R.drawable.bg_green)
            2 -> VIB_PATTERN_SEQ.setBackgroundResource(R.drawable.bg_green)
        }
    }
}