package com.example.hans

import android.annotation.SuppressLint
import android.bluetooth.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONObject
import java.util.LinkedList
import java.util.Queue
import java.util.UUID

@SuppressLint("MissingPermission")
class BleManager(private val context: Context) {

    private var bluetoothGatt: BluetoothGatt? = null
    private val adapter: BluetoothAdapter? = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
    private val handler = Handler(Looper.getMainLooper())
    private var connectionState = BluetoothProfile.STATE_DISCONNECTED

    // =================================================================
    // UUIDs
    // =================================================================
    private val SERVICE_UUID = UUID.fromString("0000fe51-0000-1000-8000-00805f9b34fb")
    private val KEEP_ALIVE   = UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb")
    private val WRITE_UUID   = UUID.fromString("0000fe03-0000-1000-8000-00805f9b34fb")
    private val PARAM_UUID   = UUID.fromString("0000fe05-0000-1000-8000-00805f9b34fb")
    private val NOTIFY_UUID  = UUID.fromString("0000fe06-0000-1000-8000-00805f9b34fb")
    private val CCCD_UUID    = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    // =================================================================

    // Command Queue to prevent Android BLE from crashing
    private val commandQueue: Queue<Runnable> = LinkedList()
    private var isExecuting = false

    fun connect(deviceAddress: String) {
        val device = adapter?.getRemoteDevice(deviceAddress)
        if (device == null) return

        commandQueue.clear()
        isExecuting = false

        bluetoothGatt = device.connectGatt(context, false, gattCallback)
    }

    // --- QUEUE SYSTEM ---
    private fun enqueueCommand(command: Runnable) {
        commandQueue.add(command)
        if (!isExecuting) {
            nextCommand()
        }
    }

    private fun nextCommand() {
        val command = commandQueue.poll()
        if (command != null) {
            isExecuting = true
            handler.post(command)
        } else {
            isExecuting = false
        }
    }
    // -------------------

    fun writeIntensity(json: JSONObject) {
        val top = json.optInt("top", 0)
        val right = json.optInt("right", 0)
        val bottom = json.optInt("bottom", 0)
        val left = json.optInt("left", 0)
        val bytes = byteArrayOf(top.toByte(), right.toByte(), bottom.toByte(), left.toByte())

        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(WRITE_UUID)
            if (char != null) {
                char.value = bytes
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                bluetoothGatt?.writeCharacteristic(char)
                Log.d("BLE", "Sent Vib: $json")

                // NO_RESPONSE writes don't trigger callbacks reliably, so we manually advance the queue
                handler.postDelayed({ nextCommand() }, 50)
            } else {
                nextCommand()
            }
        }
    }

    // Delete the old writeIntensity function, add this:

    fun writeRawCommand(bytes: ByteArray) {
        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(WRITE_UUID)
            if (char != null) {
                char.value = bytes
                // The PyBelt protocol requires DEFAULT (With Response) for vibration writes
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT

                bluetoothGatt?.writeCharacteristic(char)
                Log.d("BLE", "Wrote Vibration Command (${bytes.size} bytes)")

                // For Write With Response, the queue will advance in onCharacteristicWrite
            } else {
                nextCommand()
            }
        }
    }

    private fun subscribeTo(uuid: UUID, name: String) {
        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(uuid)
            if (char != null) {
                bluetoothGatt?.setCharacteristicNotification(char, true)
                val desc = char.getDescriptor(CCCD_UUID)
                if (desc != null) {
                    desc.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                    bluetoothGatt?.writeDescriptor(desc)
                    Log.i("BLE", "Requested Subscription to $name")
                } else {
                    nextCommand()
                }
            } else {
                nextCommand()
            }
        }
    }

    private fun writeParam(bytes: ByteArray, name: String) {
        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(PARAM_UUID)
            if (char != null) {
                char.value = bytes
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                bluetoothGatt?.writeCharacteristic(char)
                Log.i("BLE", "Sent Handshake: $name")
            } else {
                nextCommand()
            }
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            connectionState = newState
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                Log.i("BLE", "Connected. Discovering Services...")
                // Give Android 500ms to stabilize encryption/bonding before discovering
                handler.postDelayed({ gatt.discoverServices() }, 500)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                Log.i("BLE", "Disconnected. Status: $status")
                commandQueue.clear()
                isExecuting = false
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.i("BLE", "Services Ready. Queuing Initialization Protocol...")

                // 1. Subscribe to Notifications (Required for connection stability)
                subscribeTo(KEEP_ALIVE, "Keep Alive (FE02)")
                subscribeTo(NOTIFY_UUID, "Param Notify (FE06)")

                // 2. Handshake / Identity Requests
                writeParam(byteArrayOf(0x01, 0x01), "Request Belt Mode")
                writeParam(byteArrayOf(0x01, 0x02), "Request Default Intensity")
                writeParam(byteArrayOf(0x01, 0x03), "Request Heading Offset")

                // 3. === CRITICAL FIX: SWITCH TO APP MODE ===
                // Command format: [0x01 (Param Request), 0x81 (Set Mode), 0x03 (App Mode)]
                writeParam(byteArrayOf(0x01, 0x81.toByte(), 0x03), "Set Belt to APP MODE")

                // Optional: Clear any existing vibrations
                val stopBytes = byteArrayOf(0x30, 0xFF.toByte())
                enqueueCommand {
                    val char = gatt.getService(SERVICE_UUID)?.getCharacteristic(WRITE_UUID)
                    if (char != null) {
                        char.value = stopBytes
                        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                        gatt.writeCharacteristic(char)
                        Log.i("BLE", "Sent Stop All Vibrations")
                    } else {
                        nextCommand()
                    }
                }
            }
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            Log.d("BLE", "Descriptor Write Complete. Status: $status")
            // Proceed to next command in queue
            nextCommand()
        }

        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            Log.d("BLE", "Characteristic Write Complete: ${characteristic.uuid}. Status: $status")
            // Proceed to next command in queue
            nextCommand()
        }

        @Deprecated("Deprecated in Java")
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            // THE PING-PONG LISTENER
            if (characteristic.uuid == KEEP_ALIVE) {
                Log.d("BLE", "🏓 Ping Received. Enqueuing Pong.")
                // Reply to the ping to keep the connection alive
                enqueueCommand {
                    val char = gatt.getService(SERVICE_UUID)?.getCharacteristic(KEEP_ALIVE)
                    if (char != null) {
                        char.value = byteArrayOf(0x01)
                        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                        gatt.writeCharacteristic(char)
                    } else {
                        nextCommand()
                    }
                }
            }
        }
    }

    fun disconnect() {
        commandQueue.clear()
        bluetoothGatt?.disconnect()
        bluetoothGatt?.close()
        bluetoothGatt = null
    }

    fun isConnected(): Boolean {
        return connectionState == BluetoothProfile.STATE_CONNECTED
    }
}