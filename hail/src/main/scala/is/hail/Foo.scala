package is.hail;

import scala.collection.immutable.Map;

class Foo(val x: Int) {
  val b: Map[String, String] = Map.empty
  // val b: java.util.HashMap[String, String] = new java.util.HashMap[String, String]()
}
