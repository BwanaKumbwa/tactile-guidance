package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.Button

import androidx.appcompat.app.AppCompatActivity
import android.widget.RelativeLayout
import android.util.Log
import android.widget.ImageView
import android.widget.TextView
import org.json.JSONObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import android.content.res.ColorStateList
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.core.widget.ImageViewCompat

class PatternActivity : AppCompatActivity() {

    private val PATTERN_PREFS = "PatternPrefs"
    private var selectedIndex = 0
    private lateinit var VIB_PATTERN_SINGLE: RelativeLayout
    private lateinit var VIB_PATTERN_MULTI: RelativeLayout
    private lateinit var VIB_PATTERN_SEQ: RelativeLayout
    private lateinit var braceletManager: BleManager
    private lateinit var singleTitle: TextView
    private lateinit var multiTitle: TextView
    private lateinit var sequenceTitle: TextView

    private lateinit var infoSingle: TextView
    private lateinit var infoMulti: TextView
    private lateinit var infoSequence: TextView
    private lateinit var imgSingle: ImageView
    private lateinit var imgMulti: ImageView
    private lateinit var imgSequence: ImageView
    private lateinit var imgCheckSingle: ImageView
    private lateinit var imgCheckMulti: ImageView
    private lateinit var imgCheckSequence: ImageView
    private lateinit var bottomNav: BottomNavigationView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pattern)

        braceletManager = BleManagerSingleton.getBraceletManager(this)

        VIB_PATTERN_SINGLE = findViewById(R.id.button_single)
        VIB_PATTERN_MULTI = findViewById(R.id.button_multi)
        VIB_PATTERN_SEQ = findViewById(R.id.button_sequence)
        singleTitle = findViewById(R.id.single_motor)
        multiTitle = findViewById(R.id.multi_motor)
        sequenceTitle = findViewById(R.id.sequence_motor)
        infoSingle = findViewById(R.id.info_single)
        infoMulti = findViewById(R.id.info_multi)
        infoSequence = findViewById(R.id.info_sequence)
        imgSingle = findViewById(R.id.imgSingle)
        imgMulti = findViewById(R.id.imgMulti)
        imgSequence = findViewById(R.id.imgSequence)
        imgCheckSingle = findViewById(R.id.imgCheckSingle)
        imgCheckMulti = findViewById(R.id.imgCheckMulti)
        imgCheckSequence = findViewById(R.id.imgCheckSequence)

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

        bottomNav = findViewById(R.id.bottomNavigation)
        bottomNav.selectedItemId = R.id.menu_setting
        bottomNav.post {
            updateBottomNavBackground(R.id.menu_setting)
        }
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
        VIB_PATTERN_SINGLE.setBackgroundResource(R.drawable.bg_card)
        VIB_PATTERN_MULTI.setBackgroundResource(R.drawable.bg_card)
        VIB_PATTERN_SEQ.setBackgroundResource(R.drawable.bg_card)

        singleTitle.setTextColor(getColor(R.color.text_primary))
        multiTitle.setTextColor(getColor(R.color.text_primary))
        sequenceTitle.setTextColor(getColor(R.color.text_primary))

        infoSingle.setTextColor(getColor(R.color.text_secondary))
        infoMulti.setTextColor(getColor(R.color.text_secondary))
        infoSequence.setTextColor(getColor(R.color.text_secondary))

        imgSingle.setBackgroundResource(R.drawable.circle_green)
        imgMulti.setBackgroundResource(R.drawable.circle_green)
        imgSequence.setBackgroundResource(R.drawable.circle_green)

        imgSingle.setImageResource(R.drawable.ic_single)
        imgMulti.setImageResource(R.drawable.ic_multi)
        imgSequence.setImageResource(R.drawable.ic_sequence)

        ImageViewCompat.setImageTintList(
            imgSingle,
            ColorStateList.valueOf(getColor(R.color.icon)))
        ImageViewCompat.setImageTintList(
            imgMulti,
            ColorStateList.valueOf(getColor(R.color.icon)))
        ImageViewCompat.setImageTintList(
            imgSequence,
            ColorStateList.valueOf(getColor(R.color.icon)))

        imgCheckSingle.setImageResource(R.drawable.ic_circle)
        imgCheckMulti.setImageResource(R.drawable.ic_circle)
        imgCheckSequence.setImageResource(R.drawable.ic_circle)

        when (selectedIndex) {

            0 -> {
                VIB_PATTERN_SINGLE.setBackgroundResource(R.drawable.bg_default)
                singleTitle.setTextColor(getColor(R.color.text_select))
                infoSingle.setTextColor(getColor(R.color.text_subtitle))
                imgSingle.setBackgroundResource(R.drawable.circle_green_selected)
                ImageViewCompat.setImageTintList(
                    imgSingle,
                    ColorStateList.valueOf(getColor(R.color.selected_icon)))
                imgCheckSingle.setImageResource(R.drawable.ic_circle_checked)
            }

            1 -> {
                VIB_PATTERN_MULTI.setBackgroundResource(R.drawable.bg_default)
                multiTitle.setTextColor(getColor(R.color.text_select))
                infoMulti.setTextColor(getColor(R.color.text_subtitle))
                imgMulti.setBackgroundResource(R.drawable.circle_green_selected)
                ImageViewCompat.setImageTintList(
                    imgMulti,
                    ColorStateList.valueOf(getColor(R.color.selected_icon)))
                imgCheckMulti.setImageResource(R.drawable.ic_circle_checked)
            }

            2 -> {
                VIB_PATTERN_SEQ.setBackgroundResource(R.drawable.bg_default)
                sequenceTitle.setTextColor(getColor(R.color.text_select))
                infoSequence.setTextColor(getColor(R.color.text_subtitle))
                imgSequence.setBackgroundResource(R.drawable.circle_green_selected)
                ImageViewCompat.setImageTintList(
                    imgSequence,
                    ColorStateList.valueOf(getColor(R.color.selected_icon)))
                imgCheckSequence.setImageResource(R.drawable.ic_circle_checked)
            }
        }
    }
    private fun updateBottomNavBackground(page: Int) {

        val menuView = bottomNav.getChildAt(0) as ViewGroup

        val home = menuView.getChildAt(0)
        val camera = menuView.getChildAt(1)
        val setting = menuView.getChildAt(2)

        val rectangle = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle)
        val rectangleSelected = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle_selected)
        val topLeft = ContextCompat.getDrawable(this, R.drawable.bottom_top_left)
        val topLeftSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_left_selected)
        val topRight = ContextCompat.getDrawable(this, R.drawable.bottom_top_right)
        val topRightSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_right_selected)

        when (page) {
            // HOME
            R.id.menu_home -> {

                home.background = rectangleSelected
                camera.background = topLeft
                setting.background = rectangle
            }

            // CAMERA
            R.id.menu_camera -> {

                home.background = topRight
                camera.background = rectangleSelected
                setting.background = topLeft
            }

            // SETTINGS
            R.id.menu_setting -> {

                home.background = rectangle
                camera.background = topRight
                setting.background = rectangleSelected
            }
        }
    }
}